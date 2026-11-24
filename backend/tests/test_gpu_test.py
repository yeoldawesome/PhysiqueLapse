"""Settings → GPU acceleration: the image model self-test reports where it really ran."""

from __future__ import annotations

import pytest


class _Fake:
    def __init__(self, accelerator: str, fail: bool = False):
        self.accelerator = accelerator
        self.fail = fail

    def positive_probability(self, images, positive, negative):
        if self.fail:
            raise RuntimeError("DirectML device removed")
        return [0.25] * len(images)


@pytest.fixture()
def clip(monkeypatch):
    from app.services import clip_backend as C

    monkeypatch.setattr(C, "_test", None)
    monkeypatch.setattr(C, "gpu_names", lambda: ["Test GPU 9000"])
    yield C
    C.set_scorer_for_tests(None)


def test_reports_gpu_when_it_really_ran_there(client, clip, monkeypatch):
    monkeypatch.setattr(clip, "_ort_gpu_provider", lambda: "DmlExecutionProvider")
    monkeypatch.setattr(clip, "onnx_usable", lambda: True)
    clip.set_scorer_for_tests(_Fake("GPU (DirectML)"))
    assert client.get("/api/system/gpu-test").json() == {"image_model": None}  # nothing until it ran
    r = client.post("/api/system/gpu-test").json()["image_model"]
    assert r["ok"] and r["on_gpu"] and r["device"] == "GPU (DirectML)" and r["gpu_names"] == ["Test GPU 9000"]
    assert r["fell_back"] is False and r["ms_per_image"] >= 0
    assert client.get("/api/system/gpu-test").json()["image_model"] == r  # kept for the run


def test_reports_fallback_to_cpu(client, clip, monkeypatch):
    monkeypatch.setattr(clip, "_ort_gpu_provider", lambda: "DmlExecutionProvider")
    monkeypatch.setattr(clip, "onnx_usable", lambda: True)
    clip.set_scorer_for_tests(_Fake("CPU"))  # asked for the GPU, ended up on the CPU
    r = client.post("/api/system/gpu-test", params={"force": True}).json()["image_model"]
    assert r["ok"] and not r["on_gpu"] and r["fell_back"] is True


def test_reports_errors(client, clip):
    clip.set_scorer_for_tests(_Fake("GPU (DirectML)", fail=True))
    r = client.post("/api/system/gpu-test", params={"force": True}).json()["image_model"]
    assert r["ok"] is False and "device removed" in r["error"]
