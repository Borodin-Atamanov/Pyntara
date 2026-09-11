"""Production vocabulary constants of the config package.

MODES is the install mode vocabulary: production reads it to accept or
reject a mode (pyntara.py, task_catalog.py). Every other vocabulary of the
config is checked and never read by the run, so those lists live with the
checks in the test suite (tests/config_checks.py) and not in this package,
which carries no name a rule needs.
"""

from __future__ import annotations

MODES: tuple[str, ...] = ("minimal", "server", "desktop")
