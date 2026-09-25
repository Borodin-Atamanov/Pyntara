"""Tests of the shared btrfs and fstab helpers.

The helpers answer for a machine that cannot answer and write files that a
machine losing power must not lose, so the tests cover the parsing of real tool
output, the answers of a broken tool, the atomic writes and the line edits of
the fstab. Everything runs on temporary files: no test touches the machine
(docs/guards/testing-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc

from pyntara import btrfs, fstab

# A findmnt answer of a btrfs root mounted through a subvolume.
FINDMNT_ANSWER_WITH_SUBVOLUME = (
    "/dev/vda2[/@] btrfs rw,relatime,compress=zstd:15,subvolid=256\n"
)

# A findmnt answer of a plain mount of a device.
FINDMNT_ANSWER_WITHOUT_SUBVOLUME = "/dev/vda2 ext4 rw,relatime\n"


def _run_command_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    error: BaseException | None = None,
) -> list[list[str]]:
    """Answer every command with the given result; record the calls."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if error is not None:
            raise error
        return _FakeProc(returncode, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_parse_mount_line_reads_device_type_subvolume_and_options() -> None:
    # A subvolume mount is reported as /dev/vda2[/@], so the device and the
    # mounted subvolume are two facts of one field, and the subvolume carries
    # the leading slash of the path from the top level of the filesystem.
    mount = btrfs.parse_mount_line(FINDMNT_ANSWER_WITH_SUBVOLUME)

    assert mount is not None
    assert mount.device == "/dev/vda2"
    assert mount.subvolume == "/@"
    assert mount.filesystem_type == "btrfs"
    assert mount.options == ("rw", "relatime", "compress=zstd:15", "subvolid=256")


def test_parse_mount_line_reports_a_line_without_the_asked_fields() -> None:
    # A line that does not carry the three fields the command was asked for is
    # an unanswered query and not a mount with empty facts.
    assert btrfs.parse_mount_line("") is None
    assert btrfs.parse_mount_line("/dev/vda2 btrfs") is None


def test_carries_option_compares_the_name_before_the_equals_sign() -> None:
    # The option a machine carries is named by the part before the equals
    # sign, so any compression value counts as the option compress.
    mount = btrfs.parse_mount_line(FINDMNT_ANSWER_WITH_SUBVOLUME)

    assert mount is not None
    assert mount.carries_option("compress") is True
    assert mount.carries_option("subvolid") is True
    assert mount.carries_option("compressforce") is False


def test_read_mounted_filesystem_reads_the_first_answer_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # findmnt may answer with empty lines; the first line that carries a mount
    # is the answer.
    calls = _run_command_fake(
        monkeypatch,
        stdout="\n" + FINDMNT_ANSWER_WITHOUT_SUBVOLUME,
    )

    mount = btrfs.read_mounted_filesystem(["findmnt"], 5.0)

    assert calls == [["findmnt"]]
    assert mount is not None
    assert mount.filesystem_type == "ext4"
    assert mount.subvolume == ""


def test_read_mounted_filesystem_reports_a_failing_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tool that fails answers nothing, and the section that asked reports it
    # instead of reading an empty answer as a mount.
    _run_command_fake(monkeypatch, returncode=1, stdout=FINDMNT_ANSWER_WITHOUT_SUBVOLUME)

    assert btrfs.read_mounted_filesystem(["findmnt"], 5.0) is None


def test_read_mounted_filesystem_reports_a_command_that_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A command that exceeds its timeout raises subprocess.TimeoutExpired,
    # which is not a TimeoutError, and a helper that let it through would stop
    # the whole provisioning on one slow tool.
    _run_command_fake(
        monkeypatch, error=subprocess.TimeoutExpired(cmd=["findmnt"], timeout=5.0)
    )

    assert btrfs.read_mounted_filesystem(["findmnt"], 5.0) is None


def test_read_subvolume_paths_reports_a_command_that_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The listing is asked once per task and answered with an empty tuple when
    # the tool does not answer, so the caller makes its decision either way.
    _run_command_fake(
        monkeypatch, error=subprocess.TimeoutExpired(cmd=["btrfs"], timeout=5.0)
    )

    assert btrfs.read_subvolume_paths(["btrfs", "subvolume", "list", "/"], 5.0) == ()


def test_parse_subvolume_paths_reads_the_path_behind_the_separator() -> None:
    # One record of the listing is a line of fields that ends with the path.
    text = (
        "ID 256 gen 10 top level 5 path @\n"
        "ID 263 gen 12 top level 5 path @points\n"
        "ID 271 gen 13 top level 5 path @points/Pyntara-permanent\n"
        "some line without a path\n"
    )

    assert btrfs.parse_subvolume_paths(text) == (
        "@",
        "@points",
        "@points/Pyntara-permanent",
    )


def test_parse_read_only_property_reads_both_answers() -> None:
    assert btrfs.parse_read_only_property("ro=true\n") is True
    assert btrfs.parse_read_only_property(" ro=false ") is False


def test_parse_read_only_property_reports_any_other_answer() -> None:
    # Anything else is an unanswered query and not a writable subvolume, so the
    # caller does not report a point that is in fact read only.
    assert btrfs.parse_read_only_property("") is None
    assert btrfs.parse_read_only_property("ERROR: not a subvolume") is None


def test_read_only_property_reads_the_answer_of_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _run_command_fake(monkeypatch, stdout="ro=true\n")

    assert btrfs.read_only_property(["btrfs", "property", "get", "/p", "ro"], 5.0) is True
    assert calls == [["btrfs", "property", "get", "/p", "ro"]]


def test_write_file_atomically_sets_the_content_and_the_mode(tmp_path: Path) -> None:
    # A file that the boot loader runs must be executable, and the mode is set
    # on the temporary file so the target never appears with another one.
    path = tmp_path / "entry"
    path.write_text("old\n", encoding="utf-8")

    btrfs.write_file_atomically(path, "new\n", 0o755)

    assert path.read_text(encoding="utf-8") == "new\n"
    assert path.stat().st_mode & 0o777 == 0o755
    assert list(tmp_path.iterdir()) == [path]


def test_write_file_atomically_creates_the_missing_directories(tmp_path: Path) -> None:
    # A target inside a directory that a package creates only on some machines
    # is written without the caller creating the directory itself.
    path = tmp_path / "etc" / "grub.d" / "entry"

    btrfs.write_file_atomically(path, "body\n", 0o755)

    assert path.read_text(encoding="utf-8") == "body\n"


def test_write_config_directive_replaces_the_line_of_its_key(tmp_path: Path) -> None:
    # The file is a shell configuration of another package, so only the line of
    # the key changes and every other line survives.
    path = tmp_path / "config"
    path.write_text(
        "# a comment\n"
        'GRUB_BTRFS_IGNORE_SPECIFIC_PATH=("old")\n'
        'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="quiet"\n',
        encoding="utf-8",
    )

    changed, failure = btrfs.write_config_directive(
        path, "GRUB_BTRFS_IGNORE_SPECIFIC_PATH", 'GRUB_BTRFS_IGNORE_SPECIFIC_PATH=("new")'
    )

    assert changed is True
    assert failure is None
    assert path.read_text(encoding="utf-8") == (
        "# a comment\n"
        'GRUB_BTRFS_IGNORE_SPECIFIC_PATH=("new")\n'
        'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="quiet"\n'
    )


def test_write_config_directive_reports_an_unreadable_file(tmp_path: Path) -> None:
    # A section that owns one setting of a file it cannot read reports the
    # reason as its own warning and keeps the other steps running.
    changed, failure = btrfs.write_config_directive(
        tmp_path / "missing", "KEY", "KEY=value"
    )

    assert changed is False
    assert failure is not None and "cannot read" in failure


def test_render_template_substitutes_and_keeps_braces() -> None:
    # The templates of the sections carry braces of their own, such as a boot
    # entry, so the renderer uses the dollar syntax and leaves braces alone.
    text = "exec { path=$program; }\n"

    assert btrfs.render_template(text, {"program": "/usr/bin/tool"}) == (
        "exec { path=/usr/bin/tool; }\n"
    )


def test_fstab_field_values_skips_comments_and_short_lines() -> None:
    assert fstab.field_values("# a comment\n") is None
    assert fstab.field_values("UUID=x / btrfs defaults\n") is None
    assert fstab.field_values("UUID=x / btrfs defaults 0 0\n") == (
        "UUID=x",
        "/",
        "btrfs",
        "defaults",
        "0",
        "0",
    )


def test_fstab_spec_of_mount_point_reads_the_device_field() -> None:
    text = "UUID=abc / btrfs defaults 0 0\nUUID=def /home btrfs defaults 0 0\n"

    assert fstab.spec_of_mount_point(text, "/home") == "UUID=def"
    assert fstab.spec_of_mount_point(text, "/points") is None
    assert fstab.carries_mount_point(text, "/home") is True
    assert fstab.carries_mount_point(text, "/points") is False


def test_fstab_options_with_assignment_adds_one_option() -> None:
    options, changed = fstab.options_with_assignment("rw,relatime", "compress=zstd:15")

    assert changed is True
    assert options == "rw,relatime,compress=zstd:15"


def test_fstab_options_with_assignment_replaces_the_value_of_the_option() -> None:
    # An option that is already there with another value is replaced instead of
    # being added a second time, so a machine that carries an older compression
    # ends with one compression option.
    options, changed = fstab.options_with_assignment(
        "rw,compress=zstd:3,relatime", "compress=zstd:15"
    )

    assert changed is True
    assert options == "rw,compress=zstd:15,relatime"


def test_fstab_options_with_assignment_reports_an_option_already_in_force() -> None:
    options, changed = fstab.options_with_assignment("rw,compress=zstd:15", "compress=zstd:15")

    assert changed is False
    assert options == "rw,compress=zstd:15"


def test_fstab_text_with_option_changes_only_the_options_field() -> None:
    # The device, the mount point, the filesystem type and the check fields
    # stay as the machine wrote them, and the trailing newline survives.
    text = "# comment\nUUID=abc / btrfs rw 0 0\nUUID=def /home btrfs rw 0 0\n"

    new_text, changed = fstab.text_with_option(text, "/", "compress=zstd:15")

    assert changed is True
    assert new_text.endswith("\n")
    assert new_text == (
        "# comment\n"
        "UUID=abc / btrfs rw,compress=zstd:15 0 0\n"
        "UUID=def /home btrfs rw 0 0\n"
    )


def test_fstab_text_with_option_keeps_a_line_that_already_carries_it() -> None:
    text = "UUID=abc / btrfs compress=zstd:15 0 0\n"

    new_text, changed = fstab.text_with_option(text, "/", "compress=zstd:15")

    assert changed is False
    assert new_text == text


def test_fstab_text_with_option_reports_a_missing_mount_point() -> None:
    text = "UUID=abc / btrfs rw 0 0\n"

    new_text, changed = fstab.text_with_option(text, "/home", "compress=zstd:15")

    assert changed is False
    assert new_text == text
