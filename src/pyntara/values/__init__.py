"""Values of the tasks: one module per task, plain typed constants.

A task reads its own module at the point of use, so a value and its type
live on one line and adding or changing a value is one edit in one file.
The helper here never raises and never invents a value: it answers which
of the names it was given the module does not declare, and the caller
names them in plain words and carries on with the rest of its work, so a
single absent value never stops the run.

A name that is not declared is a defect of development, not a state of the
machine: the guard of the test suite refuses a value written outside this
package, and the completeness guards refuse a name that is declared but
never read and a name that is read but never declared.
"""

from __future__ import annotations


def missing_value_names(module: object, names: tuple[str, ...]) -> tuple[str, ...]:
    """Return the names the module does not declare, in the order given.

    The caller passes the names it is about to read, so the answer names
    exactly the values it cannot use. An absent name is reported by the
    caller as a warning of the step that needed it, and the remaining
    steps and tasks still run: absence is a statement, never an error.
    """

    return tuple(name for name in names if not hasattr(module, name))
