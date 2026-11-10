"""Small file-system helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace_retry(src: Path | str, dest: Path | str, timeout: float = 15.0) -> None:
    """os.replace that waits out short Windows file locks.

    Right after a large file is written, antivirus (Windows Defender) or the search indexer
    often holds it open for a moment to scan it, and renaming it then fails with
    PermissionError (WinError 32 / 5). Retry with a growing delay for up to `timeout` seconds.
    """
    delay = 0.05
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
