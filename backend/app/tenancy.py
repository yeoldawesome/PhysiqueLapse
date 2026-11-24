"""Which library (photo collection: folder + database) the current code works on.

There is one library, DATA_DIR itself (tests point it at a temporary folder). Code reads
`config.THUMBS_DIR`, `engine()` and so on, which resolve through `current()`; background jobs
carry the library they were queued in.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Library:
    id: int
    root: Path


class NoLibrary(RuntimeError):
    """Library data was touched before a library was set."""


_current: ContextVar[Library | None] = ContextVar("physiquelapse_library", default=None)
_default: Library | None = None
_lock = threading.Lock()


def set_default(lib: Library | None) -> None:
    global _default
    with _lock:
        _default = lib


def default() -> Library | None:
    return _default


def current() -> Library:
    lib = _current.get() or _default
    if lib is None:
        raise NoLibrary("no library set")
    return lib


def current_or_none() -> Library | None:
    return _current.get() or _default


@contextmanager
def use_library(lib: Library) -> Iterator[Library]:
    token = _current.set(lib)
    try:
        yield lib
    finally:
        _current.reset(token)


def all_libraries() -> list[Library]:
    """The library, if its database exists."""
    if _default is not None and (_default.root / "physiquelapse.db").exists():
        return [_default]
    return []
