"""Caller-driven non-packet structured-observability drain shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from .core import plan_drain
from .errors import InvalidSinkError, SinkWriteError
from .sinks import render_console, render_jsonl


@dataclass(frozen=True, slots=True)
class DrainResult:
    """Successful caller-driven drain outcome."""

    event_count: int


def drain(
    router: object,
    *,
    clock: object,
    jsonl_sink: object,
    console_sink: object | None = None,
    flush: object | None = None,
) -> DrainResult:
    """Drain one batch through injected sinks outside a packet path."""

    if not callable(clock):
        raise InvalidSinkError("observability clock must be callable")
    if not callable(jsonl_sink):
        raise InvalidSinkError("observability JSONL sink must be callable")
    if console_sink is not None and not callable(console_sink):
        raise InvalidSinkError("observability console sink must be callable")
    if flush is not None and not callable(flush):
        raise InvalidSinkError("observability flush callback must be callable")
    clock_value = cast(Callable[[], datetime], clock)
    jsonl_write = cast(Callable[[bytes], None], jsonl_sink)
    console_write = cast(Callable[[str], None] | None, console_sink)
    flush_write = cast(Callable[[], None] | None, flush)
    batch = plan_drain(router, clock_value())
    try:
        for event in batch.events:
            jsonl_write(render_jsonl(event))
            if console_write is not None:
                console_write(render_console(event))
        if batch.events and flush_write is not None:
            flush_write()
    except Exception as error:
        raise SinkWriteError("observability sink write failed") from error
    return DrainResult(len(batch.events))
