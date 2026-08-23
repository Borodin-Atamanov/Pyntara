"""Unit tests for the version bumping module.

The module edits plain text files in temporary directories; no external
resources are involved (docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.bump_version import (
    bump_version_in_repo,
    main,
    next_patch_version,
    read_current_version,
    set_version_in_file,
)


def make_repo(tmp_path: Path, version: str) -> tuple[Path, Path, Path, Path]:
    """A temporary package version file, installer and README, all at version."""

    package_file = tmp_path / "src" / "pyntara" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text(
        f'"""Pyntara package."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    installer_file = tmp_path / "inst.sh"
    installer_file.write_text(
        f'#!/usr/bin/env bash\n\nPYNTARA_VERSION="{version}"\n', encoding="utf-8"
    )
    readme_file = tmp_path / "README.md"
    readme_file.write_text(f"# Pyntara version {version}\n", encoding="utf-8")
    return tmp_path, package_file, installer_file, readme_file


def test_next_patch_version_increments_patch() -> None:
    assert next_patch_version("0.1.0") == "0.1.1"
    assert next_patch_version("1.2.3") == "1.2.4"
    assert next_patch_version("0.0.0") == "0.0.1"


@pytest.mark.parametrize("version", ["", "1", "1.2", "1.2.x", "1.2.3.4", "1..3"])
def test_next_patch_version_rejects_invalid(version: str) -> None:
    with pytest.raises(ValueError):
        next_patch_version(version)


def test_read_current_version_reads_version_line(tmp_path: Path) -> None:
    _, package_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert read_current_version(package_file) == "0.1.0"


def test_read_current_version_missing_line_is_an_error(tmp_path: Path) -> None:
    package_file = tmp_path / "no_version.py"
    package_file.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_current_version(package_file)


def test_set_version_in_file_replaces_version_line(tmp_path: Path) -> None:
    _, package_file, _, _ = make_repo(tmp_path, "0.1.0")
    changed = set_version_in_file(
        package_file, '__version__ = "', '__version__ = "0.1.1"'
    )
    assert changed is True
    assert read_current_version(package_file) == "0.1.1"
    assert '"""Pyntara package."""' in package_file.read_text(encoding="utf-8")


def test_set_version_in_file_missing_line_is_untouched(tmp_path: Path) -> None:
    target = tmp_path / "no_version.txt"
    target.write_text("some line\n", encoding="utf-8")
    changed = set_version_in_file(target, '__version__ = "', '__version__ = "0.1.1"')
    assert changed is False
    assert target.read_text(encoding="utf-8") == "some line\n"


def test_bump_version_in_repo_updates_package_installer_and_readme(
    tmp_path: Path,
) -> None:
    root, package_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(package_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.1"' in installer_file.read_text(encoding="utf-8")
    assert "# Pyntara version 0.1.1" in readme_file.read_text(encoding="utf-8")


def test_bump_version_in_repo_keeps_installer_without_version_line(
    tmp_path: Path,
) -> None:
    root, package_file, _, _ = make_repo(tmp_path, "0.1.0")
    installer_file = root / "inst.sh"
    installer_file.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(package_file) == "0.1.1"
    assert installer_file.read_text(encoding="utf-8") == "#!/usr/bin/env bash\n"


def test_bump_version_in_repo_keeps_readme_without_title_line(
    tmp_path: Path,
) -> None:
    root, package_file, _, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.write_text("# Not a version title\n", encoding="utf-8")
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(package_file) == "0.1.1"
    assert readme_file.read_text(encoding="utf-8") == "# Not a version title\n"


def test_bump_version_in_repo_without_readme_skips_it(tmp_path: Path) -> None:
    root, package_file, installer_file, readme_file = make_repo(tmp_path, "0.1.0")
    readme_file.unlink()
    new_version = bump_version_in_repo(root)
    assert new_version == "0.1.1"
    assert read_current_version(package_file) == "0.1.1"
    assert 'PYNTARA_VERSION="0.1.1"' in installer_file.read_text(encoding="utf-8")


def test_main_print_only_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, package_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root), "--print-only"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_current_version(package_file) == "0.1.0"


def test_main_bumps_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, package_file, _, _ = make_repo(tmp_path, "0.1.0")
    assert main(["--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_current_version(package_file) == "0.1.1"
