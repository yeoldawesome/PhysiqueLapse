"""open_clip zero-shot scoring, fully offline at runtime.

Weights are fetched once by scripts/download_models.py into data/models/open_clip.
At runtime HF_HUB_OFFLINE is forced so nothing reaches the network.
"""

from __future__ import annotations

import logging
import os
import threading
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
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(config.CLIP_MODEL_NAME)
        self.torch = torch
        self._text_cache: dict[tuple[str, ...], np.ndarray] = {}
        self._lock = threading.Lock()

    def _text(self, prompts: tuple[str, ...]) -> np.ndarray:
        if prompts not in self._text_cache:
            with self.torch.no_grad():
                t = self.model.encode_text(self.tokenizer(list(prompts)))
                t = t / t.norm(dim=-1, keepdim=True)
            self._text_cache[prompts] = t.numpy()
        return self._text_cache[prompts]

    def positive_probability(self, images: list[Image.Image], positive: list[str], negative: list[str]) -> list[float]:
        positive = [p.strip() for p in positive if p.strip()]
        negative = [p.strip() for p in negative if p.strip()]
        if not images or not positive:
            return [0.0] * len(images)
        prompts = tuple(positive + negative)
        with self._lock, self.torch.no_grad():
            text = self._text(prompts)
            batch = self.torch.stack([self.preprocess(im.convert("RGB")) for im in images])
            f = self.model.encode_image(batch)
            f = (f / f.norm(dim=-1, keepdim=True)).numpy()
        logits = 100.0 * f @ text.T
        logits -= logits.max(axis=1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(axis=1, keepdims=True)
        return [float(x) for x in p[:, : len(positive)].sum(axis=1)]


_scorer: ClipScorer | None = None
_lock = threading.Lock()


def get_scorer() -> ClipScorer:
    global _scorer
    with _lock:
        if _scorer is None:
            _scorer = OpenClipScorer()
        return _scorer


def available() -> bool:
    """True when abs detection can run: a scorer is loaded (or injected) or weights are on disk."""
    return _scorer is not None or local_weights() is not None


def set_scorer_for_tests(s: ClipScorer | None) -> None:
    global _scorer
    _scorer = s
