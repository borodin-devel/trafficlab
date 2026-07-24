"""Bounded structured application diagnostics."""

from .core import DrainBatch, EmitResult, EventRouter, plan_drain
from .errors import (
    InvalidEventError,
    InvalidPolicyError,
    InvalidSeverityError,
    InvalidSinkError,
    ObservabilityError,
    SinkWriteError,
)
from .service import DrainResult, drain
from .sinks import render_console, render_jsonl
from .values import ObservabilityPolicy, Severity, StructuredEvent, severity_rank

__all__ = (
    "DrainBatch",
    "DrainResult",
    "EmitResult",
    "EventRouter",
    "InvalidEventError",
    "InvalidPolicyError",
    "InvalidSeverityError",
    "InvalidSinkError",
    "ObservabilityError",
    "ObservabilityPolicy",
    "Severity",
    "SinkWriteError",
    "StructuredEvent",
    "drain",
    "plan_drain",
    "render_console",
    "render_jsonl",
    "severity_rank",
)
