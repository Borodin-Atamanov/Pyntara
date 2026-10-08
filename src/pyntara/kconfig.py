"""KConfig access shared by every task that reads or writes a KDE config file.

A task that touches a KConfig file runs kreadconfig6 or kwriteconfig6 as the
desktop user, with the home of that user in the environment, and it reads a key
before writing it to keep the task idempotent. The reader and the writer share
one argv builder, so a reader and a writer of the same task can never drift
apart, and every section builds its calls the same way.

The vocabulary of the tools (the two base calls, the group and key flags and
the boolean type flag) lives in values/common.py; a caller passes the file name,
the group segments, the key and, when its file needs them, extra flags such as
the notify or the delete flag of the settings section.
"""

from __future__ import annotations

from collections.abc import Sequence

from pyntara.utils import (
    as_user_command,
    home_environment,
    run_command,
    substituted_command,
    trim_whitespace,
)
from pyntara.values import common as common_values


def kconfig_command(
    base_command: Sequence[str],
    file_name: str,
    group_segments: Sequence[str],
    key: str,
    *,
    extra_flags: Sequence[str] = (),
) -> list[str]:
    """One KConfig call: the base, the file, the groups, the key and extra flags.

    The base call carries the file as {file_name} and every selector is a value
    of the shared module, so another KConfig version or another tool is a value
    change. The reader and the writer share this builder, so the two calls can
    never drift apart, and a section adds its own flags, for example the
    boolean type or the notify flag, through extra_flags.
    """

    command = substituted_command(base_command, {"file_name": file_name})
    for segment in group_segments:
        command.extend(
            substituted_command(common_values.CONFIG_GROUP_FLAG, {"group": segment})
        )
    command.extend(substituted_command(common_values.CONFIG_KEY_FLAG, {"key": key}))
    command.extend(extra_flags)
    return command


def read_config_value(
    file_name: str,
    group_segments: Sequence[str],
    key: str,
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> str:
    """Current value of one KConfig key of the desktop user, or an empty text.

    env is the live session environment when the caller has one; without it the
    call runs with the home of the desktop user, which is what a KConfig read
    needs.
    """

    command = kconfig_command(
        common_values.KREADCONFIG_COMMAND, file_name, group_segments, key
    )
    result = run_command(
        as_user_command(command),
        extra_env=env if env is not None else home_environment(),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def write_config_value(
    file_name: str,
    group_segments: Sequence[str],
    key: str,
    value: str,
    *,
    timeout: float,
    extra_flags: Sequence[str] = (),
    env: dict[str, str] | None = None,
) -> None:
    """Write one KConfig key of the desktop user.

    extra_flags carries the flags a file needs, such as the boolean type flag;
    env is the live session environment when the caller has one.
    """

    command = kconfig_command(
        common_values.KWRITECONFIG_COMMAND,
        file_name,
        group_segments,
        key,
        extra_flags=extra_flags,
    )
    command.append(value)
    run_command(
        as_user_command(command),
        extra_env=env if env is not None else home_environment(),
        timeout=timeout,
    )


def delete_config_value(
    file_name: str,
    group_segments: Sequence[str],
    key: str,
    *,
    timeout: float,
    extra_flags: Sequence[str] = (),
    env: dict[str, str] | None = None,
) -> None:
    """Delete one KConfig key of the desktop user.

    extra_flags carries the flags a file needs, such as the delete flag and the
    notify flag of the settings section.
    """

    command = kconfig_command(
        common_values.KWRITECONFIG_COMMAND,
        file_name,
        group_segments,
        key,
        extra_flags=extra_flags,
    )
    run_command(
        as_user_command(command),
        extra_env=env if env is not None else home_environment(),
        timeout=timeout,
    )
