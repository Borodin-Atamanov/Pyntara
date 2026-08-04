"""Shared data types for the provisioning engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskResult:
    """Outcome of one task execution.

    Tasks return this from task(ctx); the runner collects one per task and
    the entry point prints the summary.
    """

    success: bool
    changed: bool = False
    message: str | None = None
    error: str | None = None
