import numpy as np
from sqlmodel import select

from app.models import Alignment, Photo, PoseCategory
from app.services import grouping
from app.services.grouping import JOINTS, Group, PoseSet, cluster, fit_errors, merge, suggest_names
from app.settings import AppSettings


def test_grouping_defaults_to_minimum_20_photos(client):
    status = client.get("/api/grouping").json()
    preview = client.get("/api/grouping/preview").json()

    assert status["min_size"] == 20
    assert preview["min_size"] == 20


def test_default_rotation_clamp_handles_camera_rotation_up_to_45_degrees():
    points = np.column_stack((np.cos(np.arange(len(JOINTS))), np.sin(np.arange(len(JOINTS)))))
    angle = np.deg2rad(30)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    rotated = points @ rotation.T
    visible = np.ones(len(JOINTS), dtype=bool)

    error_45 = fit_errors(points, visible, rotated[None, ...], visible[None, ...], np.array([1.0]), AppSettings().rotation_clamp_deg)[0]
    error_15 = fit_errors(points, visible, rotated[None, ...], visible[None, ...], np.array([1.0]), 15.0)[0]

    assert AppSettings().rotation_clamp_deg == 45.0
    assert error_45 < 1e-8
    assert error_15 > error_45 + 0.05


def test_grouping_prioritizes_upper_body_and_does_not_require_hips():
    points_by_name = {
        "nose": (0.0, 2.7),
        "left_eye": (-0.1, 2.9),
        "right_eye": (0.1, 2.9),
        "left_ear": (-0.3, 2.8),
        "right_ear": (0.3, 2.8),
        "left_shoulder": (-0.9, 2.0),
        "right_shoulder": (0.9, 2.0),
        "left_elbow": (-1.2, 1.1),
        "right_elbow": (1.2, 1.1),
        "left_wrist": (-1.5, 0.2),
        "right_wrist": (1.5, 0.2),
        "left_hip": (-0.6, -1.0),
        "right_hip": (0.6, -1.0),
    }
    points = np.array([points_by_name[name] for name in JOINTS], dtype=np.float64)
    visible = np.ones(len(JOINTS), dtype=bool)
    landmarks = {
        name: {"x_px": point[0], "y_px": point[1], "visibility": 1.0}
        for name, point in points_by_name.items()
    }
    torso_scale = grouping.upper_body_scale(landmarks)
    torso = np.array([torso_scale])

    def distance(a, b):
        forward = fit_errors(a, visible, b[None, ...], visible[None, ...], torso, 45.0)[0]
        reverse = fit_errors(b, visible, a[None, ...], visible[None, ...], torso, 45.0)[0]
        return (forward + reverse) / 2

    hips_changed = points.copy()
    hips_changed[[JOINTS.index("left_hip"), JOINTS.index("right_hip")], 1] -= 1.5
    hips_changed_landmarks = {name: point.copy() for name, point in landmarks.items()}
    for name in ("left_hip", "right_hip"):
        hips_changed_landmarks[name]["y_px"] -= 1.5
    arms_changed = points.copy()
    arms_changed[[JOINTS.index("left_elbow"), JOINTS.index("left_wrist")], 0] -= 2.0
    hip_distance = distance(points, hips_changed)
    arm_distance = distance(points, arms_changed)

    without_hips = visible.copy()
    without_hips[[JOINTS.index("left_hip"), JOINTS.index("right_hip"), JOINTS.index("left_shoulder")]] = False
    no_hip_fit = fit_errors(points, visible, points[None, ...], without_hips[None, ...], torso, 45.0)[0]

    assert hip_distance < grouping.DEFAULT_THRESHOLD
    assert arm_distance > grouping.DEFAULT_THRESHOLD
    assert grouping.upper_body_scale(hips_changed_landmarks) == torso_scale
    assert no_hip_fit < 1.0


def test_clustering_ungroups_small_groups_and_uses_generic_names():
    pose_set = PoseSet(
        ids=[1, 2, 3, 4],
        pts=np.zeros((4, 1, 2)),
        vis=np.ones((4, 1), dtype=bool),
        torso=np.ones(4),
    )
    distances = np.array([
        [0.0, 0.01, 0.8, 0.8],
        [0.01, 0.0, 0.8, 0.8],
        [0.8, 0.8, 0.0, 0.7],
        [0.8, 0.8, 0.7, 0.0],
    ])

    groups, ungrouped = cluster(pose_set, distances, threshold=0.1, min_size=2)
    suggest_names(groups)

    assert [group.members for group in groups] == [[1, 2]]
    assert groups[0].name == "Group 1"
    assert set(ungrouped) == {3, 4}


def test_group_names_ignore_previous_pose_labels():
    groups = [Group(members=[1], medoid=1, spread=0, name="Front Double Biceps")]

    suggest_names(groups)

    assert groups[0].name == "Group 1"


def test_merge_moves_photos_and_marks_their_alignments_stale(session):
    source = PoseCategory(name="Group 1")
    target = PoseCategory(name="Group 2")
    session.add(source)
    session.add(target)
    session.commit()
    session.refresh(source)
    session.refresh(target)

    photo = Photo(
        sha256="test-merge-photo",
        original_path="originals/test.jpg",
        original_filename="test.jpg",
        ext=".jpg",
        pose_category_id=source.id,
    )
    session.add(photo)
    session.commit()
    session.refresh(photo)
    session.add(Alignment(photo_id=photo.id, pose_category_id=source.id, stale=False))
    session.commit()

    assert merge(session, source.id, target.id) == 1
    assert session.get(PoseCategory, source.id) is None
    assert session.get(Photo, photo.id).pose_category_id == target.id
    assert session.get(Alignment, photo.id).stale


def test_new_automatic_groups_default_to_25_percent_framing(session, monkeypatch):
    photos = [
        Photo(sha256=f"group-framing-{i}", original_path=f"originals/{i}.jpg", original_filename=f"{i}.jpg", ext=".jpg")
        for i in range(2)
    ]
    session.add_all(photos)
    session.commit()
    for photo in photos:
        session.refresh(photo)

    pose_set = PoseSet(
        ids=[photo.id for photo in photos],
        pts=np.zeros((2, 1, 2)),
        vis=np.ones((2, 1), dtype=bool),
        torso=np.ones(2),
    )
    distances = np.array([[0.0, 0.01], [0.01, 0.0]])
    monkeypatch.setattr(grouping, "cached_matrix", lambda _: (pose_set, distances))

    grouping.apply(session, threshold=0.15, min_size=2)

    groups = session.exec(select(PoseCategory).where(PoseCategory.name.like("Group %"))).all()
    assert len(groups) == 1
    assert groups[0].framing_zoom == 0.25
