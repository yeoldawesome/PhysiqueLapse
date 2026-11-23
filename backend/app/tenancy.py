"""Which library (photo collection: folder + database) the current code works on.

The desktop app has one library: DATA_DIR itself. The server gives every account its own
library under DATA_DIR/libraries/<id>/ with the same layout. The library is chosen per request
(from the logged-in user) and per background job (from the job's queue entry) and held in a
context variable, so all existing code - which reads `config.THUMBS_DIR`, `engine()` and so
on - works on the right library without knowing about users. Nothing can fall back to a
shared folder: in server mode, touching a library path with no library chosen is an error.
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
    id: int  # 0 = the single library of the desktop app (and of the server before accounts)
    root: Path


class NoLibrary(RuntimeError):
    """Library data was touched outside a request or job that chose a library."""


_current: ContextVar[Library | None] = ContextVar("physiquelapse_library", default=None)
_default: Library | None = None
_lock = threading.Lock()


def set_default(lib: Library | None) -> None:
    """The library used when none is chosen (desktop app, tests, single-user server)."""
    global _default
    with _lock:
        _default = lib


def default() -> Library | None:
    return _default


def current() -> Library:
    lib = _current.get() or _default
    if lib is None:
        raise NoLibrary("no library chosen for this request or job")
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


def set_for_request(lib: Library | None):
    """Choose the library for the rest of this request (returns a token for reset)."""
    return _current.set(lib)


def reset(token) -> None:
    _current.reset(token)


def library_for_user(user_id: int) -> Library:
    from . import config

    return Library(user_id, config.DATA_DIR / "libraries" / str(user_id))


def all_libraries() -> list[Library]:
    """Every library on disk: the default one (if set and present) plus one per account."""
    from . import config

    out: list[Library] = []
    if _default is not None and (_default.root / "physiquelapse.db").exists():
        out.append(_default)
    base = config.DATA_DIR / "libraries"
    if base.is_dir():
        for d in sorted(base.iterdir(), key=lambda p: p.name):
            if d.is_dir() and d.name.isdigit() and (d / "physiquelapse.db").exists():
                out.append(Library(int(d.name), d))
    return out
