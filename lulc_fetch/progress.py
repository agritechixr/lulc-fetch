"""Progress reporting and cooperative cancellation for long-running work.

Library code calls `update(fraction, message)` at natural checkpoints (per strip, per band, per
file chunk). Whoever runs the work (e.g. the web app's job runner) installs a handler for its thread
that records progress and raises `Cancelled` when the user cancels. Without a handler, calls are no-ops.

`span(start, end)` maps the progress of a sub-step onto part of the overall range, so steps can be
nested without knowing about each other:

    with span(0.0, 0.3):
        pick_best_scene(...)      # its update(1.0) means 30 % overall
"""

from __future__ import annotations

import threading
from contextlib import contextmanager


class Cancelled(Exception):
    """Raised inside the work when the user cancelled it."""


_local = threading.local()


def set_handler(fn) -> None:
    """Install `fn(fraction_or_None, message_or_None)` for the current thread (None to remove)."""
    _local.fn = fn
    _local.spans = [(0.0, 1.0)]


def update(fraction: float | None = None, message: str | None = None) -> None:
    fn = getattr(_local, "fn", None)
    if fn is None:
        return
    if fraction is not None:
        start, end = _local.spans[-1]
        fraction = start + max(0.0, min(1.0, fraction)) * (end - start)
    fn(fraction, message)


@contextmanager
def span(start: float, end: float):
    spans = getattr(_local, "spans", None)
    if spans is None:
        yield
        return
    s0, e0 = spans[-1]
    spans.append((s0 + start * (e0 - s0), s0 + end * (e0 - s0)))
    try:
        yield
    finally:
        spans.pop()
    update(end)  # the sub-step is complete: report the end of its range (in the parent's scale)
