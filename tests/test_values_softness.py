"""Proofs that a value which is not declared costs a task and never the run.

The run stays soft while development stays strict (docs/TODO.md, decision 37):
a value that is not declared is reported in plain words by the task that
needed it, the task changes nothing, and the remaining tasks still run. The
values package is imported whole or not at all (decision 38), so a values
module that does not import costs the tasks that import it.

Every migrated section is named below, so a section that loses its guard fails
this suite instead of looking finished.
"""

from __future__ import annotations

import importlib

import pytest
from support import make_config, make_context

from pyntara import task_runner
from pyntara.context import Context

# Every migrated section: the task module and the values module it reads.
MIGRATED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("add_extra_repos", "add_extra_repos"),
    ("cli_tools", "cli_tools"),
    ("ffmpeg_setup", "ffmpeg_setup"),
    ("hostname", "hostname"),
    ("imagemagick_setup", "imagemagick_setup"),
    ("kde_keyboard_setup", "kde_keyboard_setup"),
    ("local_vault_setup", "local_vault_setup"),
    ("nextdns_setup_system_wide", "nextdns_setup_system_wide"),
    ("playwright_setup", "playwright_setup"),
    ("rustdesk_setup", "rustdesk_setup"),
    ("scrcpy_setup", "scrcpy_setup"),
    ("sotavpn_setup", "sotavpn_setup"),
    ("ssh_client_setup", "ssh_client_setup"),
    ("swapfile_service_install", "swapfile_service_install"),
    ("telegram_setup", "telegram_setup"),
    ("zram_service", "zram_service"),
    ("zswap_service", "zswap_service"),
)

# A name no task module carries, used to prove the run reaches the next task.
NOT_WRITTEN_TASK_NAME = "a_task_that_no_module_carries"


def _ctx() -> Context:
    # The start delay is zeroed so the runner does not pause between the two
    # tasks of the proof. No task of this file reaches the machine: the guard
    # returns before the first step of a task.
    return make_context(config=make_config(task_start_delay_seconds=0))


@pytest.mark.parametrize(("task_name", "values_module_name"), MIGRATED_SECTIONS)
def test_a_value_that_is_not_declared_costs_the_task_and_never_the_run(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    values_module_name: str,
) -> None:
    # Every declared name is removed, not one: a read that stands above the
    # guard of the task then fails the test with an AttributeError instead of
    # reaching the machine.
    values_module = importlib.import_module(f"pyntara.values.{values_module_name}")
    task_module = importlib.import_module(f"pyntara.tasks.{task_name}")
    removed_names = tuple(values_module.READ_VALUE_NAMES)
    for name in removed_names:
        monkeypatch.delattr(values_module, name)

    result = task_module.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert "not declared" in (result.message or "")
    for name in removed_names:
        assert any(name in warning for warning in result.warnings), (
            task_name,
            name,
            result.warnings,
        )


def test_a_values_module_that_cannot_import_costs_only_its_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The import of the task module is made to fail the way it fails when a
    # values module cannot be imported: the import machinery is replaced, so
    # no task of this proof ever runs against the machine.
    questions_asked: list[str] = []

    def fake_import(module_name: str) -> object:
        questions_asked.append(module_name)
        if module_name == "pyntara.tasks.hostname":
            raise ModuleNotFoundError(
                "import of pyntara.values.hostname halted",
                name="pyntara.values.hostname",
            )
        # Every other name is a task module nobody wrote yet.
        raise ModuleNotFoundError(f"No module named {module_name!r}", name=module_name)

    monkeypatch.setattr(task_runner.importlib, "import_module", fake_import)

    results = task_runner.run_tasks(_ctx(), ["hostname", NOT_WRITTEN_TASK_NAME])

    assert questions_asked == ["pyntara.tasks.hostname", "pyntara.tasks." + NOT_WRITTEN_TASK_NAME]
    assert [name for name, _ in results] == ["hostname", NOT_WRITTEN_TASK_NAME]
    broken = results[0][1]
    assert broken.success is True
    assert any("import failed" in warning for warning in broken.warnings), (
        broken.warnings
    )
    # The reason names the module that failed, never the task as unwritten.
    assert "pyntara.values.hostname" in " ".join(broken.warnings)
    assert "not implemented" not in (broken.message or "")
    # The run reached the next task, which has no module at all, so the task
    # module itself was missing and the task is reported as skipped.
    assert results[1][1].skipped is True
