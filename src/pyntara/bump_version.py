"""Version bumping for the repository.

The pre-commit hook (hooks/pre-commit) calls bump_build_version before
every commit, so the patch step grows with each commit. The carrier it
writes is src/pyntara/_version.py, a file whose whole content is the
version line, and .gitattributes marks that file merge=union: a merge or
a rebase of two branches that both grew the number completes without a
conflict, and the file may briefly hold several version lines in any
order. read_current_version therefore answers the highest version it
finds, and every write rebuilds the file with a single version line.

The landing step (hooks/land_version_commit.sh) calls
bump_version_in_repo once on the branch tip before the push to main:
that call bumps the carrier and mirrors the new number into the
PYNTARA_VERSION line of inst.sh and the title line of README.md. Those
two carriers are whole line machine owned and only the landing step
writes them, because the installer runs on a bare machine and prints its
version as the very first line, where no number can be derived, and
because only landed content is ever downloaded. A carrier whose version
line was rewritten by hand stops the landing instead of silently keeping
the old number: bump_version_in_repo reads every carrier back and raises
ValueError naming the ones that do not carry the new number.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from pyntara.config_edit import replace_line_by_string

_VERSION_PATTERN = re.compile(r'__version__ = "([^"]+)"')
_BUILD_VERSION_FILE = Path("src/pyntara/_version.py")
_INSTALLER_VERSION_FILE = Path("inst.sh")
_README_VERSION_FILE = Path("README.md")
_README_TITLE_PREFIX = "# Pyntara "
_INSTALLER_VERSION_PREFIX = 'PYNTARA_VERSION="'


def version_lines() -> dict[Path, str]:
    """The version line each carrier must hold, as a template with {version}.

    The landing step verifies these templates after a bump, so a carrier
    whose line was rewritten by hand stops matching and the landing
    fails loudly instead of mirroring nothing.
    """

    return {
        _BUILD_VERSION_FILE: '__version__ = "{version}"',
        _INSTALLER_VERSION_FILE: f'{_INSTALLER_VERSION_PREFIX}{{version}}"',
        _README_VERSION_FILE: f"{_README_TITLE_PREFIX}{{version}}",
    }


def version_carrier_paths() -> tuple[Path, ...]:
    """The files that carry the version, relative to the repository root."""

    return (_BUILD_VERSION_FILE, _INSTALLER_VERSION_FILE, _README_VERSION_FILE)


def build_carrier_path() -> Path:
    """The single carrier the pre-commit hook bumps on every commit."""

    return _BUILD_VERSION_FILE


def existing_carrier_paths(root: Path) -> tuple[Path, ...]:
    """The carriers present in root; a repository without README is normal."""

    return tuple(path for path in version_carrier_paths() if (root / path).exists())


def parse_version(version: str) -> tuple[int, int, int]:
    """The three numbers of a dotted triple; ValueError on any other shape."""

    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid version: {version}")
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def next_patch_version(version: str) -> str:
    """The next patch version of a dotted triple; ValueError on any other shape."""

    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def read_current_version(version_file: Path) -> str:
    """The highest version line of the file; ValueError when it has none.

    A union merge leaves the version lines of both branches in the
    carrier, in an arbitrary order, so the highest one is the current
    version whichever line the merge wrote first.
    """

    text = version_file.read_text(encoding="utf-8")
    versions = [match.group(1) for match in _VERSION_PATTERN.finditer(text)]
    if not versions:
        raise ValueError(f"version string not found in {version_file}")
    return max(versions, key=parse_version)


def write_build_version(build_file: Path, new_version: str) -> None:
    """Write the carrier with one version line, dropping union duplicates."""

    kept = [
        line
        for line in build_file.read_text(encoding="utf-8").splitlines()
        if not _VERSION_PATTERN.search(line)
    ]
    kept.append(f'__version__ = "{new_version}"')
    build_file.write_text("\n".join(kept) + "\n", encoding="utf-8")


def bump_build_version(root: Path) -> str:
    """Bump the build carrier alone and return the new version."""

    build_file = root / build_carrier_path()
    new_version = next_patch_version(read_current_version(build_file))
    write_build_version(build_file, new_version)
    return new_version


def set_version_in_file(path: Path, needle: str, slide: str) -> bool:
    """Replace the version line of path with slide; return whether it changed.

    A missing version line appends nothing: replace_line_by_string is
    called with add_slide_if_no_needle disabled, so a file without the
    line stays untouched instead of gaining an orphan line at the end.
    """

    text = path.read_text(encoding="utf-8")
    new_text, changed = replace_line_by_string(
        text, needle, slide, add_slide_if_no_needle=False
    )
    if changed:
        path.write_text(new_text, encoding="utf-8")
    return changed


def carriers_missing_version(root: Path, version: str) -> tuple[Path, ...]:
    """The carriers that do not carry the version line of version.

    A carrier that is not there is skipped: the version tool is not
    allowed to invent a file.
    """

    missing: list[Path] = []
    for carrier, template in version_lines().items():
        path = root / carrier
        if not path.exists():
            continue
        if template.format(version=version) not in path.read_text(encoding="utf-8"):
            missing.append(carrier)
    return tuple(missing)


def bump_version_in_repo(root: Path) -> str:
    """Bump the version and mirror it into every carrier; return the new one.

    The build carrier is the source of truth. The installer line and the
    README title follow it, one whole line each. Every carrier that is
    present must carry the new number afterwards: ValueError names the
    ones that do not, so a version line rewritten by hand stops the
    landing instead of silently keeping an old number.
    """

    new_version = bump_build_version(root)
    set_version_in_file(
        root / _INSTALLER_VERSION_FILE,
        _INSTALLER_VERSION_PREFIX,
        f'{_INSTALLER_VERSION_PREFIX}{new_version}"',
    )
    readme_file = root / _README_VERSION_FILE
    if readme_file.exists():
        set_version_in_file(
            readme_file, _README_TITLE_PREFIX, f"{_README_TITLE_PREFIX}{new_version}"
        )
    missing = carriers_missing_version(root, new_version)
    if missing:
        names = ", ".join(str(carrier) for carrier in missing)
        raise ValueError(f"version line not updated in: {names}")
    return new_version


def main(argv: list[str] | None = None) -> int:
    """Bump the version, or answer a print request, and print the result."""

    parser = argparse.ArgumentParser(
        description="Bump the pyntara patch version in the repository."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="bump the build carrier alone, the way the pre-commit hook does",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--print-only",
        action="store_true",
        help="print the next version without writing any file",
    )
    output.add_argument(
        "--print-carrier-paths",
        action="store_true",
        help="print the carriers present, one per line, without writing",
    )
    output.add_argument(
        "--print-build-carrier",
        action="store_true",
        help="print the carrier the pre-commit hook bumps",
    )
    args = parser.parse_args(argv)
    if args.print_carrier_paths:
        for carrier in existing_carrier_paths(args.root):
            print(carrier)
        return 0
    if args.print_build_carrier:
        print(build_carrier_path())
        return 0
    try:
        if args.print_only:
            new_version = next_patch_version(
                read_current_version(args.root / build_carrier_path())
            )
        elif args.build_only:
            new_version = bump_build_version(args.root)
        else:
            new_version = bump_version_in_repo(args.root)
    except ValueError as error:
        print(f"pyntara.bump_version: {error}", file=sys.stderr)
        return 1
    print(new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
