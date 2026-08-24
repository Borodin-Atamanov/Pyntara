"""Shared data types for the provisioning engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskResult:
    """Outcome of one task execution.

    Tasks return this from task(ctx); the runner collects one per task and
    the entry point prints the summary. skipped marks a task whose module is
    not implemented: it could not run, which is not a failure. warnings
    lists the steps of a completed task that could not be performed: a
    recoverable failure must never stop the provisioning, so a task that
    ran reports success with warnings, and the entry point counts the
    warnings and exits nonzero so scripts can detect an incomplete
    configuration.
    """

    success: bool
    changed: bool = False
    skipped: bool = False
    message: str | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()
