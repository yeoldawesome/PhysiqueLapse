"""CLIP zero-shot scoring, fully offline at runtime.

Two interchangeable backends:
- OnnxClipScorer: the model exported to ONNX (scripts/export_clip_onnx.py), run by ONNX Runtime
  on the GPU when there is one (DirectML on Windows: NVIDIA, AMD and Intel; CUDA with
  onnxruntime-gpu), else on the CPU. No PyTorch needed; the desktop app ships this.
- OpenClipScorer: open_clip + PyTorch (source checkouts, Docker). Uses CUDA when torch has it.
  Weights are fetched once by scripts/download_models.py into data/models/; at runtime
  HF_HUB_OFFLINE is forced so nothing reaches the network.

PL_CLIP_DEVICE=cpu forces the CPU for either backend.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from .. import config
from ..fsutil import replace_retry

log = logging.getLogger(__name__)


class ClipScorer(Protocol):
    def positive_probability(self, images: list[Image.Image], positive: list[str], negative: list[str]) -> list[float]: ...


def _long(p) -> str:
    """Windows extended-length path so files deep in the HF cache can be read."""
    s = str(p.resolve()) if hasattr(p, "resolve") else str(p)
    prefix = "\\\\?\\"
    return prefix + s if os.name == "nt" and not s.startswith(prefix) else s


def local_weights():
    """Path of the CLIP weights file, or None. Uses the flat copy in data/models/;
    if only the Hugging Face cache has it, makes the flat copy first."""
    import shutil

    flat = config.clip_weights_path()
    if flat.is_file() and flat.stat().st_size > 1_000_000:
        return flat
    if not config.CLIP_CACHE_DIR.exists():
        return None
    for root, _dirs, files in os.walk(_long(config.CLIP_CACHE_DIR)):
        if "open_clip_model.safetensors" in files:
            src = os.path.join(root, "open_clip_model.safetensors")
            if os.path.getsize(src) > 1_000_000:
                tmp = flat.with_suffix(".part")
                shutil.copyfile(src, tmp)
                replace_retry(tmp, flat)
                return flat
    return None


class OpenClipScorer:
    def __init__(self) -> None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HOME"] = str(config.CLIP_CACHE_DIR / "hf")
        import open_clip
        import torch

        torch.set_num_threads(config.cpu_threads(reserve=2))
        use_cuda = _want_gpu() and torch.cuda.is_available()
        self.device = torch.device("cuda" if use_cuda else "cpu")
        self.accelerator = f"GPU (CUDA: {torch.cuda.get_device_name(0)})" if use_cuda else "CPU"
        weights = local_weights()
        if weights is None:
            raise RuntimeError(
                f"CLIP weights not found in {config.MODELS_DIR}. Run: .venv\\Scripts\\python backend\\scripts\\download_models.py"
            )
        try:
            # load the cached file by path: no hub lookup at all (offline-safe, no network calls)
            model, _, preprocess = open_clip.create_model_and_transforms(config.CLIP_MODEL_NAME, pretrained=str(weights))
        except Exception as e:  # weights missing while offline
            raise RuntimeError(
                f"CLIP weights at {weights} could not be loaded ({e}). Re-run backend\\scripts\\download_models.py"
            ) from e
        model.eval()
        self.model = model.to(self.device)
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(config.CLIP_MODEL_NAME)
        self.torch = torch
        self._text_cache: dict[tuple[str, ...], np.ndarray] = {}
        self._lock = threading.Lock()

    def _text(self, prompts: tuple[str, ...]) -> np.ndarray:
        if prompts not in self._text_cache:
            with self.torch.no_grad():
                t = self.model.encode_text(self.tokenizer(list(prompts)).to(self.device))
                t = t / t.norm(dim=-1, keepdim=True)
            self._text_cache[prompts] = t.float().cpu().numpy()
        return self._text_cache[prompts]

    def positive_probability(self, images: list[Image.Image], positive: list[str], negative: list[str]) -> list[float]:
        positive = [p.strip() for p in positive if p.strip()]
        negative = [p.strip() for p in negative if p.strip()]
        if not images or not positive:
            return [0.0] * len(images)
        prompts = tuple(positive + negative)
        with self._lock, self.torch.no_grad():
            text = self._text(prompts)
            batch = self.torch.stack([self.preprocess(im.convert("RGB")) for im in images]).to(self.device)
            f = self.model.encode_image(batch)
            f = (f / f.norm(dim=-1, keepdim=True)).float().cpu().numpy()
        return _positive_share(f, text, len(positive))


def _positive_share(img_feats: np.ndarray, text_feats: np.ndarray, n_positive: int) -> list[float]:
    """Softmax over all prompts (CLIP's logit scale 100), summed over the positive ones."""
    logits = 100.0 * img_feats @ text_feats.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    return [float(x) for x in p[:, :n_positive].sum(axis=1)]


def _want_gpu() -> bool:
    return (os.environ.get("PL_CLIP_DEVICE") or "auto").strip().lower() != "cpu"


# ONNX Runtime execution providers to try, best first. onnxruntime-directml (Windows) brings Dml,
# onnxruntime-gpu brings CUDA; the CPU provider is always there.
_GPU_PROVIDERS = {"CUDAExecutionProvider": "CUDA", "DmlExecutionProvider": "DirectML"}


def _ort_gpu_provider() -> str | None:
    if not _want_gpu():
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    have = ort.get_available_providers()
    return next((p for p in _GPU_PROVIDERS if p in have), None)


class OnnxClipScorer:
    """CLIP through ONNX Runtime. Tries the GPU first; if the GPU can't load or run the model
    (old driver, too little memory, ...) it logs why and carries on on the CPU."""

    def __init__(self, model_dir: Path | None = None) -> None:
        import json

        from .clip_tokenizer import ClipTokenizer

        d = model_dir or config.clip_onnx_dir()
        if d is None:
            raise RuntimeError("CLIP ONNX models not found (run backend/scripts/export_clip_onnx.py)")
        self.dir = Path(d)
        self.cfg = json.loads((self.dir / "clip_onnx.json").read_text())
        self.tokenizer = ClipTokenizer(self.dir / "bpe_simple_vocab_16e6.txt.gz", self.cfg.get("context_length", 77))
        self._text_cache: dict[tuple[str, ...], np.ndarray] = {}
        self._lock = threading.Lock()
        self._mean = np.asarray(self.cfg["mean"], np.float32).reshape(1, 1, 3)
        self._std = np.asarray(self.cfg["std"], np.float32).reshape(1, 1, 3)
        gpu = _ort_gpu_provider()
        if gpu:
            try:
                self._load(gpu)
                # a GPU that can't actually run the graph often only fails on the first run
                self._run_visual(np.zeros((1, 3, self.size, self.size), np.float32))
                return
            except Exception as e:
                log.warning("CLIP on the GPU (%s) failed, using the CPU instead: %s", gpu, e)
        self._load(None)

    @property
    def size(self) -> int:
        return int(self.cfg["image_size"])

    def _load(self, gpu: str | None) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        providers: list = ["CPUExecutionProvider"]
        if gpu == "DmlExecutionProvider":
            # DirectML needs these (ONNX Runtime docs); prefer the discrete GPU on laptops
            so.enable_mem_pattern = False
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers.insert(0, (gpu, {"performance_preference": "high_performance", "device_filter": "gpu"}))
        elif gpu:
            providers.insert(0, gpu)
        else:
            so.intra_op_num_threads = config.cpu_threads(reserve=2)

        def session(name: str):
            try:
                return ort.InferenceSession(str(self.dir / name), so, providers=providers)
            except Exception:
                if gpu != "DmlExecutionProvider":
                    raise
                # older onnxruntime-directml without the adapter options
                return ort.InferenceSession(str(self.dir / name), so, providers=[gpu, "CPUExecutionProvider"])

        self.visual = session("visual.onnx")
        self.textual = session("textual.onnx")
        used = self.visual.get_providers()[0]
        if gpu and used != gpu:
            raise RuntimeError(f"{gpu} not usable")
        self.gpu = gpu
        self.accelerator = f"GPU ({_GPU_PROVIDERS[gpu]})" if gpu else "CPU"
        self._text_cache.clear()
        log.info("CLIP (ONNX) running on %s", self.accelerator)

    def _with_fallback(self, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            if not self.gpu:
                raise
            log.warning("CLIP on the GPU failed mid-run, switching to the CPU: %s", e)
            self._load(None)
            return fn(*a)

    def _run_visual(self, batch: np.ndarray) -> np.ndarray:
        return self.visual.run(None, {self.visual.get_inputs()[0].name: batch})[0]

    def _run_text(self, tokens: np.ndarray) -> np.ndarray:
        return self.textual.run(None, {self.textual.get_inputs()[0].name: tokens})[0]

    def preprocess(self, im: Image.Image) -> np.ndarray:
        """open_clip's eval transform: resize the short side (bicubic), centre crop, normalise.
        Same arithmetic as torchvision's Resize + CenterCrop on a PIL image."""
        im = im.convert("RGB")
        s = self.size
        w, h = im.size
        if w <= h:
            nw, nh = s, int(s * h / w)
        else:
            nw, nh = int(s * w / h), s
        if (nw, nh) != (w, h):
            im = im.resize((nw, nh), Image.Resampling.BICUBIC)
        top, left = int(round((nh - s) / 2.0)), int(round((nw - s) / 2.0))
        im = im.crop((left, top, left + s, top + s))
        a = (np.asarray(im, np.float32) / 255.0 - self._mean) / self._std
        return a.transpose(2, 0, 1)

    def _text(self, prompts: tuple[str, ...]) -> np.ndarray:
        if prompts not in self._text_cache:
            t = self._with_fallback(self._run_text, self.tokenizer(list(prompts))).astype(np.float32)
            self._text_cache[prompts] = t / np.linalg.norm(t, axis=-1, keepdims=True)
        return self._text_cache[prompts]

    def positive_probability(self, images: list[Image.Image], positive: list[str], negative: list[str]) -> list[float]:
        positive = [p.strip() for p in positive if p.strip()]
        negative = [p.strip() for p in negative if p.strip()]
        if not images or not positive:
            return [0.0] * len(images)
        prompts = tuple(positive + negative)
        batch = np.stack([self.preprocess(im) for im in images]).astype(np.float32)
        with self._lock:
            text = self._text(prompts)
            f = self._with_fallback(self._run_visual, batch).astype(np.float32)
        f /= np.linalg.norm(f, axis=-1, keepdims=True)
        return _positive_share(f, text, len(positive))


def onnx_usable() -> bool:
    if config.clip_onnx_dir() is None:
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


_scorer: ClipScorer | None = None
_lock = threading.Lock()


def get_scorer() -> ClipScorer:
    global _scorer
    with _lock:
        if _scorer is None:
            _scorer = OnnxClipScorer() if onnx_usable() else OpenClipScorer()
        return _scorer


def available() -> bool:
    """True when abs detection can run: a scorer is loaded (or injected) or weights are on disk."""
    return _scorer is not None or onnx_usable() or local_weights() is not None


def accelerator() -> str:
    """Where CLIP runs (or will run once loaded), for the Settings page."""
    if _scorer is not None:
        return getattr(_scorer, "accelerator", "CPU")
    if onnx_usable():
        gpu = _ort_gpu_provider()
        return f"GPU ({_GPU_PROVIDERS[gpu]}), if the driver allows" if gpu else "CPU"
    return "CPU (PyTorch; GPU if it has CUDA)"


def set_scorer_for_tests(s: ClipScorer | None) -> None:
    global _scorer
    _scorer = s
