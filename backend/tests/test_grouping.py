import numpy as np

from app.services.grouping import Group, PoseSet, cluster, suggest_names


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
