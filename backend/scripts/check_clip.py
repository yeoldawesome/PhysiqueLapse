"""Check CLIP (ONNX) on this machine's accelerator against the CPU: where it runs, how fast,
and whether the results agree. Used by the Mac build; handy anywhere.

    python backend/scripts/check_clip.py packaging/clip_onnx
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    import numpy as np
    from PIL import Image

    from app.services.clip_backend import OnnxClipScorer

    d = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    rng = np.random.default_rng(0)
    ims = [Image.fromarray(rng.integers(0, 255, (400, 320, 3), dtype=np.uint8)) for _ in range(8)]
    pos, neg = ["a shirtless man flexing"], ["a photo of food", "a screenshot"]

    os.environ["PL_CLIP_DEVICE"] = "cpu"
    cpu = OnnxClipScorer(d)
    ref = cpu.positive_probability(ims, pos, neg)
    os.environ["PL_CLIP_DEVICE"] = "auto"
    t0 = time.time()
    acc = OnnxClipScorer(d)
    load = time.time() - t0
    acc.positive_probability(ims[:1], pos, neg)  # warm-up
    t0 = time.time()
    got = acc.positive_probability(ims, pos, neg)
    run = time.time() - t0
    t0 = time.time()
    cpu.positive_probability(ims, pos, neg)
    run_cpu = time.time() - t0
    diff = float(np.max(np.abs(np.asarray(ref) - np.asarray(got))))
    print(f"CLIP runs on: {acc.accelerator} (load {load:.1f} s)")
    print(f"8 images: {run:.2f} s on {acc.accelerator}, {run_cpu:.2f} s on CPU; max score difference {diff:.4f}")
    if diff > 0.05:
        print("FAIL: the accelerator's scores differ from the CPU's", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
