"""Export the CLIP model to ONNX so it runs on any GPU through ONNX Runtime, without PyTorch.

    python backend/scripts/export_clip_onnx.py                       # -> data/models/clip_onnx
    python backend/scripts/export_clip_onnx.py --out packaging/clip_onnx   # what build.ps1 runs

Needs torch + open_clip (the normal backend install) and the CLIP weights (downloaded here if
missing). Writes visual.onnx, textual.onnx, the tokenizer vocabulary and clip_onnx.json, then
checks ONNX Runtime's output against PyTorch's. --random skips the weights (tests, CI smoke).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402

OPSET = 17


def export(out: Path, random_weights: bool = False, check: bool = True) -> Path:
    import numpy as np
    import open_clip
    import torch
    from open_clip import tokenizer as oc_tok

    from app.services.clip_backend import local_weights

    if random_weights:
        model, _, preprocess = open_clip.create_model_and_transforms(config.CLIP_MODEL_NAME)
    else:
        weights = local_weights()
        if weights is None:
            from app.services import model_files as MF

            config.ensure_dirs()
            m = next(m for m in MF.required() if m.key == "clip")
            print(f"downloading {m.url}")
            MF.download(m.url, m.path)
            weights = m.path
        model, _, preprocess = open_clip.create_model_and_transforms(config.CLIP_MODEL_NAME, pretrained=str(weights))
    model.eval()
    tok = open_clip.get_tokenizer(config.CLIP_MODEL_NAME)
    pcfg = model.visual.preprocess_cfg
    size = model.visual.image_size
    size = size[0] if isinstance(size, (tuple, list)) else size
    assert pcfg.get("resize_mode", "shortest") == "shortest" and pcfg.get("interpolation", "bicubic") == "bicubic", pcfg

    model.requires_grad_(False)

    class Visual(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = model

        def forward(self, image):
            return self.m.encode_image(image)

    class Textual(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = model

        def forward(self, text):
            return self.m.encode_text(text)

    out.mkdir(parents=True, exist_ok=True)
    img = torch.randn(2, 3, size, size)
    txt = tok(["a photo of a person", "a plain red square"])
    kw = dict(opset_version=OPSET, do_constant_folding=True, dynamo=False)
    with torch.no_grad():
        torch.onnx.export(Visual(), (img,), str(out / "visual.onnx"), input_names=["image"], output_names=["embedding"],
                          dynamic_axes={"image": {0: "batch"}, "embedding": {0: "batch"}}, **kw)
        torch.onnx.export(Textual(), (txt,), str(out / "textual.onnx"), input_names=["text"], output_names=["embedding"],
                          dynamic_axes={"text": {0: "batch"}, "embedding": {0: "batch"}}, **kw)
    shutil.copyfile(Path(oc_tok.__file__).parent / "bpe_simple_vocab_16e6.txt.gz", out / "bpe_simple_vocab_16e6.txt.gz")
    (out / "clip_onnx.json").write_text(json.dumps({
        "model": config.CLIP_MODEL_NAME,
        "pretrained": "random" if random_weights else config.CLIP_PRETRAINED,
        "image_size": int(size),
        "mean": list(pcfg.get("mean", open_clip.OPENAI_DATASET_MEAN)),
        "std": list(pcfg.get("std", open_clip.OPENAI_DATASET_STD)),
        "context_length": int(model.context_length),
        "opset": OPSET,
    }, indent=2))

    if check:
        from PIL import Image

        from app.services.clip_backend import OnnxClipScorer

        sc = OnnxClipScorer(out)
        rng = np.random.default_rng(0)
        ims = [Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8)) for h, w in ((300, 500), (640, 480), (224, 224))]
        prompts = ["a shirtless man flexing", "a plain red square", "café, naïve—test!! 123"]
        assert (sc.tokenizer(prompts) == tok(prompts).numpy()).all(), "tokenizer mismatch"
        with torch.no_grad():
            ref_img = model.encode_image(torch.stack([preprocess(i) for i in ims])).numpy()
            ref_txt = model.encode_text(tok(prompts)).numpy()
        got_img = sc._run_visual(np.stack([sc.preprocess(i) for i in ims]))
        got_txt = sc._run_text(sc.tokenizer(prompts))
        for name, a, b in (("image", ref_img, got_img), ("text", ref_txt, got_txt)):
            err = float(np.abs(a - b).max() / (np.abs(a).max() + 1e-9))
            print(f"  {name} embeddings: max relative difference {err:.2e} ({sc.accelerator})")
            assert err < 1e-3, f"{name} embeddings differ from PyTorch ({err})"
    total = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f"CLIP ONNX written to {out} ({total:.0f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: data/models/clip_onnx)")
    ap.add_argument("--random", action="store_true", help="random weights (no download; for tests)")
    ap.add_argument("--no-check", action="store_true")
    a = ap.parse_args()
    export((a.out or config.MODELS_DIR / "clip_onnx").resolve(), a.random, not a.no_check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
