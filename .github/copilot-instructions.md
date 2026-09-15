# Working on this repository

AGENTS.md is the authority for every action; read it first, then README.md and
the document the task needs. This file adds the standing order the user gives
for the value work, so it does not have to be repeated. The user does not repeat
it: it is loaded with every session and it is the order to continue with.

## Standing order

Work in cycles without stopping and without asking for the next command. Take
one cohesive block that is certainly within reach, put it in order completely,
prove it with tests, apply common sense, push, then choose the next block
deliberately and continue. The goal of the whole effort is that every value the
run uses comes from config/ instead of being written in the code
(docs/contracts/architecture.md, Configuration; the closed list of types is
docs/spec/config-content.md).

Two fronts belong to the same order: the values above, and the recoverable
failure policy of the task contract (architecture contract, Task contract): a
step that cannot run is a warning of a completed task and the missing mechanism
skips that step alone. The second front is held closed by
tests/test_runner.py::test_task_modules_report_findings_in_warnings, which
demands that no module under src/pyntara/tasks/ builds an error result.

## Cycle of one block

1. Audit one module or one vocabulary of values still written in code.
2. State the decisions before implementing them.
3. Move each value into config/: the TOML key with its comment, the dataclass
field with the docstring, the check in tests/config_checks.py, the mirror line
in tests/config_helpers.py, and the section literal of the matching
tests/test_config_*.py when that literal is built by hand.
4. Read the value from the config in the code and delete the copy in the code.
5. Prove the change with tests: another value in the config must change the
behaviour, and a missing tool or a failed step must never stop the run.
6. Run the full gate: uv run pytest -q -n auto -m 'not live', uv run ruff check
src tests, uv run mypy, uv run mypy --strict src/, bash tests/test_inst.sh, bash
tests/test_pre_commit_hook.sh, bash tests/test_commit_script.sh.
7. Prove a deployed artifact live on the target machine when the block touches
a unit, a service, a path or a command. A green suite never proves the machine.
8. push to main, and check that HEAD equals origin/main.

## Where the progress lives

/memories/session/config-migration-progress.md carries the state of the front:
the blocks already closed with their commits, the gotchas met, and the next
block. Read it before choosing work and update it after every block.

## While the front is open

Do not end the turn between blocks and do not ask whether to continue; the
user gave this order once and does not repeat it. Close several blocks in one
turn when they are within reach, report in one message at the end of the group
with the commits and the live checks, and name the next block explicitly. End
the turn only when the front is closed or when a decision of the user is
required, and say plainly which decision that is.
