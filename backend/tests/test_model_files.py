"""First-run model downloads (desktop app) and the frozen-app paths."""


def test_missing_models_and_download(client, tmp_path):
    from app.services import model_files as MF

    r = client.get("/api/system/models").json()  # the test data folder has no models
    assert {m["key"] for m in r["missing"]} == {"pose_heavy", "face_detector", "face_recognizer", "clip"}
    assert r["missing_mb"] > 600

    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 3_000_000)
    dest = tmp_path / "models" / "f.bin"
    seen = []
    MF.download(src.as_uri(), dest, lambda got, total: seen.append(got))
    assert dest.read_bytes() == src.read_bytes() and not dest.with_suffix(".bin.part").exists()
    assert seen and seen[-1] == 3_000_000


def test_data_dir_defaults():
    from app import config

    assert not config.FROZEN  # tests run from source: data stays in the project unless DATA_DIR is set
