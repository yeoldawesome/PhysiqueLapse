"""CLIP through ONNX Runtime: tokenizer, preprocessing, scoring, GPU->CPU fallback, export."""

from __future__ import annotations

import gzip
import json

import numpy as np
import pytest
from PIL import Image

ort = pytest.importorskip("onnxruntime")
onnx = pytest.importorskip("onnx")


def _tiny_models(d, size=32, dim=4):
    """Stand-in models with CLIP's interface: image [N,3,S,S] -> [N,dim], text [N,77] -> [N,dim]."""
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(1)
    w_img = numpy_helper.from_array(rng.normal(size=(3, dim)).astype(np.float32), "w")
    g = helper.make_graph(
        [helper.make_node("ReduceMean", ["image"], ["m"], axes=[2, 3], keepdims=0), helper.make_node("MatMul", ["m", "w"], ["embedding"])],
        "visual", [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["batch", 3, size, size])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", dim])], [w_img],
    )
    onnx.save(helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)]), d / "visual.onnx")
    w_txt = numpy_helper.from_array(rng.normal(size=(77, dim)).astype(np.float32), "w")
    g = helper.make_graph(
        [helper.make_node("Cast", ["text"], ["f"], to=TensorProto.FLOAT), helper.make_node("MatMul", ["f", "w"], ["embedding"])],
        "textual", [helper.make_tensor_value_info("text", TensorProto.INT64, ["batch", 77])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", dim])], [w_txt],
    )
    onnx.save(helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)]), d / "textual.onnx")
    with gzip.open(d / "bpe_simple_vocab_16e6.txt.gz", "wt", encoding="utf-8") as f:
        f.write("#version: 0.2\nt h\nth e</w>\na b\n")
    (d / "clip_onnx.json").write_text(json.dumps({"image_size": size, "mean": [0.5] * 3, "std": [0.25] * 3, "context_length": 77}))
    return d


def test_scores_and_preprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("PL_CLIP_DEVICE", "cpu")
    from app.services.clip_backend import OnnxClipScorer

    sc = OnnxClipScorer(_tiny_models(tmp_path))
    assert sc.accelerator == "CPU"
    x = sc.preprocess(Image.new("RGB", (90, 40), (255, 0, 0)))
    assert x.shape == (3, 32, 32) and x.dtype == np.float32
    assert np.allclose(x[0], 2.0) and np.allclose(x[1], -2.0)  # (1 - 0.5) / 0.25, (0 - 0.5) / 0.25
    ims = [Image.new("RGB", (50, 70), c) for c in ((255, 0, 0), (0, 0, 255))]
    p = sc.positive_probability(ims, ["the a"], ["b", " "])
    assert len(p) == 2 and all(0.0 <= v <= 1.0 for v in p)
    assert sc.positive_probability(ims, [], ["b"]) == [0.0, 0.0]
    ids = sc.tokenizer(["the", "THE  the"])
    assert ids[0, 0] == sc.tokenizer.sot_id and ids[0, 2] == sc.tokenizer.eot_id
    assert ids[0, 1] == sc.tokenizer.encoder["the</w>"] and (ids[1, 1:3] == ids[0, 1]).all()


def test_gpu_unavailable_falls_back_to_cpu(tmp_path, monkeypatch):
    from app.services import clip_backend

    monkeypatch.setattr(clip_backend, "_ort_gpu_provider", lambda: "DmlExecutionProvider")
    sc = clip_backend.OnnxClipScorer(_tiny_models(tmp_path))
    if "DmlExecutionProvider" not in ort.get_available_providers():
        assert sc.accelerator == "CPU" and sc.gpu is None
    assert len(sc.positive_probability([Image.new("RGB", (40, 40))], ["a"], ["b"])) == 1


def test_gpu_failing_mid_run_switches_to_cpu(tmp_path, monkeypatch):
    monkeypatch.setenv("PL_CLIP_DEVICE", "cpu")
    from app.services.clip_backend import OnnxClipScorer

    sc = OnnxClipScorer(_tiny_models(tmp_path))
    sc.gpu = "DmlExecutionProvider"  # pretend the GPU is in use and then breaks
    real, calls = sc._run_visual, []

    def flaky(batch):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("device removed")
        return real(batch)

    monkeypatch.setattr(sc, "_run_visual", flaky)
    assert len(sc.positive_probability([Image.new("RGB", (40, 40))], ["a"], ["b"])) == 1
    assert sc.gpu is None and sc.accelerator == "CPU"


def test_onnx_dir_preferred(data_dir, monkeypatch):
    from app import config
    from app.services import clip_backend, model_files

    assert config.clip_onnx_dir() is None
    _tiny_models(_mk(config.MODELS_DIR / "clip_onnx"))
    assert config.clip_onnx_dir() == config.MODELS_DIR / "clip_onnx"
    assert clip_backend.available()
    assert model_files.present(next(m for m in model_files.required() if m.key == "clip"))
    monkeypatch.setattr(clip_backend, "_scorer", None)
    assert isinstance(clip_backend.get_scorer(), clip_backend.OnnxClipScorer)
    monkeypatch.setattr(clip_backend, "_scorer", None)


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_tokenizer_matches_open_clip():
    open_clip = pytest.importorskip("open_clip")
    from pathlib import Path

    from app.services.clip_tokenizer import ClipTokenizer

    ref = open_clip.get_tokenizer("ViT-B-32")
    mine = ClipTokenizer(Path(open_clip.tokenizer.__file__).parent / "bpe_simple_vocab_16e6.txt.gz")
    texts = ["a shirtless man flexing his abs", "Café &amp; naïve—TEST!! 123 it's", "", "x " * 100, "日本語のテキスト 💪"]
    assert (mine(texts) == ref(texts).numpy()).all()


@pytest.mark.slow
def test_export_matches_torch(tmp_path):
    """Exports ViT-B-32 (random weights) and checks ONNX Runtime against PyTorch (pytest -m slow)."""
    pytest.importorskip("open_clip")
    from scripts.export_clip_onnx import export

    export(tmp_path / "clip_onnx", random_weights=True, check=True)


def test_one_at_a_time_matches_batched(tmp_path, monkeypatch):
    """Core ML path: batch pinned to 1, items run one by one; same results as a batched run."""
    monkeypatch.setenv("PL_CLIP_DEVICE", "cpu")
    from app.services.clip_backend import OnnxClipScorer

    sc = OnnxClipScorer(_tiny_models(tmp_path))
    ims = [Image.new("RGB", (60, 40 + 10 * i), (40 * i, 90, 200 - 30 * i)) for i in range(4)]
    batched = sc.positive_probability(ims, ["a", "the"], ["b"])
    sc._one_at_a_time = True
    sc._text_cache.clear()
    assert np.allclose(sc.positive_probability(ims, ["a", "the"], ["b"]), batched)

