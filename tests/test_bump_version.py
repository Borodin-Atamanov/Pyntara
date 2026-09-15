"""Unit tests for the version bumping module.

The module edits plain text files in temporary directories; no external
resources are involved (docs/guides/developer-guide.md). The last tests
read the real repository files, because the shapes of the carriers and the
union attribute of .gitattributes are part of the mechanism, not of a
fixture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pyntara.bump_version import (
    build_carrier_path,
    bump_build_version,
    bump_version_in_repo,
    carriers_missing_version,
    existing_carrier_paths,
    main,
    next_patch_version,
    read_current_version,
    set_version_in_file,
    version_carrier_paths,
    version_lines,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_CARRIER = Path("src/pyntara/_version.py")
INSTALLER_CARRIER = Path("inst.sh")
README_CARRIER = Path("README.md")
BUILD_LINE = re.compile(r'^__version__ = "(\d+\.\d+\.\d+)"$')
INSTALLER_LINE = re.compile(r'^PYNTARA_VERSION="(\d+\.\d+\.\d+)"$', re.MULTILINE)
README_LINE = re.compile(r"^# Pyntara (\d+\.\d+\.\d+)$", re.MULTILINE)


def matched_version(text: str, pattern: re.Pattern[str]) -> str:
    """The version a pattern finds in text; the caller knows it is there."""

    match = pattern.search(text)
    assert match is not None
    return match.group(1)


def make_repo(tmp_path: Path, version: str) -> tuple[Path, Path, Path, Path]:
    """A temporary build carrier, installer and README, all at version."""

    carrier_file = tmp_path / BUILD_CARRIER
    carrier_file.parent.mkdir(parents=True)
    carrier_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    installer_file = tmp_path / INSTALLER_CARRIER
    installer_file.write_text(
        f'#!/usr/bin/env bash\n\nPYNTARA_VERSION="{version}"\n', encoding="utf-8"
    )
    readme_file = tmp_path / README_CARRIER
    readme_file.write_text(f"# Pyntara {version}\n", encoding="utf-8")
    return tmp_path, carrier_file, installer_file, readme_file


def test_next_patch_version_increments_patch() -> None:
    assert next_patch_version("0.1.0") == "0.1.1"
    assert next_patch_version("1.2.3") == "1.2.4"
    assert next_patch_version("0.0.0") == "0.0.1"


@pytest.mark.parametrize("version", ["", "1", "1.2", "1.2.x", "1.2.3.4", "1..3"])
def test_next_patch_version_rejects_invalid(version: str) -> None:
    with pytest.raises(ValueError):
        next_patch_version(version)


def test_read_current_version_reads_version_line(tmp_path: Path) -> None:
    _, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert read_current_version(carrier_file) == "0.1.0"


def test_read_current_version_takes_the_highest_line(tmp_path: Path) -> None:
    # A union merge leaves the version lines of both branches in the
    # carrier, in whatever order git wrote them.
    _, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    carrier_file.write_text(
        '__version__ = "0.1.7"\n__version__ = "0.1.2"\n', encoding="utf-8"
    )
    assert read_current_version(carrier_file) == "0.1.7"
    carrier_file.write_text(
        '__version__ = "0.1.2"\n__version__ = "0.1.7"\n', encoding="utf-8"
    )
    assert read_current_version(carrier_file) == "0.1.7"


def test_read_current_version_missing_line_is_an_error(tmp_path: Path) -> None:
    carrier_file = tmp_path / "no_version.py"
    carrier_file.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_current_version(carrier_file)


def test_bump_build_version_writes_the_carrier_alone(tmp_path: Path) -> None:
    # This is the call the pre-commit hook makes: the two carriers the
    # landing step owns must stay untouched.
    root, carrier_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    assert bump_build_version(root) == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.0"' in installer_file.read_text(encoding="utf-8")
    assert readme_file.read_text(encoding="utf-8") == "# Pyntara 0.1.0\n"


def test_bump_build_version_normalizes_union_duplicates(tmp_path: Path) -> None:
    root, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    carrier_file.write_text(
        '__version__ = "0.1.9"\n__version__ = "0.1.2"\n', encoding="utf-8"
    )
    assert bump_build_version(root) == "0.1.10"
    assert carrier_file.read_text(encoding="utf-8") == '__version__ = "0.1.10"\n'


def test_set_version_in_file_replaces_version_line(tmp_path: Path) -> None:
    _, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    changed = set_version_in_file(
        carrier_file, '__version__ = "', '__version__ = "0.1.1"'
    )
    assert changed is True
    assert read_current_version(carrier_file) == "0.1.1"


def test_set_version_in_file_missing_line_is_untouched(tmp_path: Path) -> None:
    target = tmp_path / "no_version.txt"
    target.write_text("some line\n", encoding="utf-8")
    changed = set_version_in_file(target, '__version__ = "', '__version__ = "0.1.1"')
    assert changed is False
    assert target.read_text(encoding="utf-8") == "some line\n"


def test_bump_version_in_repo_updates_carrier_installer_and_readme(
    tmp_path: Path,
) -> None:
    root, carrier_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.1"' in installer_file.read_text(encoding="utf-8")
    assert "# Pyntara 0.1.1" in readme_file.read_text(encoding="utf-8")


def test_bump_version_in_repo_fails_when_the_installer_line_is_gone(
    tmp_path: Path,
) -> None:
    # An installer line rewritten by hand must stop the landing instead of
    # letting the release keep an old number without a word.
    root, _, _, _ = make_repo(tmp_path, "0.1.0")
    (root / INSTALLER_CARRIER).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inst.sh"):
        bump_version_in_repo(root)


def test_bump_version_in_repo_fails_when_the_readme_title_is_gone(
    tmp_path: Path,
) -> None:
    root, _, _, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.write_text("# Not a version title\n", encoding="utf-8")
    with pytest.raises(ValueError, match="README.md"):
        bump_version_in_repo(root)


def test_bump_version_in_repo_without_readme_skips_it(tmp_path: Path) -> None:
    root, carrier_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.unlink()
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.1"' in installer_file.read_text(encoding="utf-8")


def test_carriers_missing_version_names_every_lagging_carrier(
    tmp_path: Path,
) -> None:
    root, _, _, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.write_text("# Not a version title\n", encoding="utf-8")
    assert carriers_missing_version(root, "0.1.0") == (README_CARRIER,)
    assert carriers_missing_version(root, "9.9.9") == (
        BUILD_CARRIER,
        INSTALLER_CARRIER,
        README_CARRIER,
    )


def test_carriers_missing_version_is_empty_after_a_bump(tmp_path: Path) -> None:
    root, _, _, _ = make_repo(tmp_path, "0.1.0")
    assert bump_version_in_repo(root) == "0.1.1"
    assert carriers_missing_version(root, "0.1.1") == ()


def test_main_print_only_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root), "--print-only"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.0"


def test_main_bumps_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.1"


def test_main_build_only_bumps_the_carrier_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, carrier_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root), "--build-only"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_current_version(carrier_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.0"' in installer_file.read_text(encoding="utf-8")
    assert readme_file.read_text(encoding="utf-8") == "# Pyntara 0.1.0\n"


def test_main_reports_a_broken_carrier_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, _, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.write_text("# Not a version title\n", encoding="utf-8")
    assert main(["--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert "README.md" in captured.err
    assert captured.out == ""


def test_version_carrier_paths_are_build_installer_and_readme() -> None:
    assert version_carrier_paths() == (
        BUILD_CARRIER,
        INSTALLER_CARRIER,
        README_CARRIER,
    )
    assert build_carrier_path() == BUILD_CARRIER
    assert set(version_lines()) == set(version_carrier_paths())


def test_existing_carrier_paths_drop_a_missing_readme(tmp_path: Path) -> None:
    _, _, _, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.unlink()
    assert existing_carrier_paths(tmp_path) == (BUILD_CARRIER, INSTALLER_CARRIER)


def test_main_print_carrier_paths_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, carrier_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root), "--print-carrier-paths"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        str(BUILD_CARRIER),
        str(INSTALLER_CARRIER),
        str(README_CARRIER),
    ]
    assert read_current_version(carrier_file) == "0.1.0"


def test_main_print_build_carrier_answers_the_hook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root), "--print-build-carrier"]) == 0
    assert capsys.readouterr().out.strip() == str(BUILD_CARRIER)


def test_main_rejects_two_print_actions(tmp_path: Path) -> None:
    root, _, _, _ = make_repo(tmp_path, "0.1.0")
    with pytest.raises(SystemExit):
        main(["--root", str(root), "--print-only", "--print-carrier-paths"])


def test_repository_carriers_match_their_shapes() -> None:
    # The shapes are the contract the landing step verifies: a carrier
    # rewritten in another form has to fail the gate, not pass unnoticed.
    carrier_text = (REPOSITORY_ROOT / BUILD_CARRIER).read_text(encoding="utf-8")
    assert BUILD_LINE.search(carrier_text)
    installer_text = (REPOSITORY_ROOT / INSTALLER_CARRIER).read_text(encoding="utf-8")
    assert INSTALLER_LINE.search(installer_text)
    readme_text = (REPOSITORY_ROOT / README_CARRIER).read_text(encoding="utf-8")
    assert README_LINE.search(readme_text)


def test_repository_installed_face_keeps_one_number() -> None:
    # inst.sh and README.md are written by the landing step alone, so the
    # two of them always name the same version, on main and on a branch;
    # the build carrier may be ahead of them between landings.
    installer_text = (REPOSITORY_ROOT / INSTALLER_CARRIER).read_text(encoding="utf-8")
    readme_text = (REPOSITORY_ROOT / README_CARRIER).read_text(encoding="utf-8")
    assert matched_version(installer_text, INSTALLER_LINE) == matched_version(
        readme_text, README_LINE
    )


def test_repository_marks_the_build_carrier_for_union_merge() -> None:
    # Without the attribute two branches would conflict on the carrier on
    # every rebase, which is the defect this mechanism removes.
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert f"{BUILD_CARRIER} merge=union" in attributes
