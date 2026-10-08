"""Registering an official apt repository of a third-party browser.

chrome_setup and firefox_setup both download the signing key of the vendor,
place it in a keyring under /usr/share/keyrings, render a deb822 source from a
template and write the root-owned files only when their content differs. The
key of one vendor is armored as downloaded while the other is armored and
dearmored through gpg, so a section passes its own commands and names; the
sequence itself lives here once.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path
from string import Template

from pyntara.utils import (
    apply_owner,
    download_command,
    run_command,
    substituted_command,
)
from pyntara.values import common as common_values


def render_source_text(template_path: Path, keyring_path: Path) -> str:
    """The deb822 apt source of a vendor repository.

    The body of the source file lives in the template under task_data/ and only
    the keyring path is substituted, so the suite, the components and the
    archive address stay with the template.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(keyring_path=str(keyring_path))


def download_keyring(
    *,
    url: str,
    keyring_path: Path,
    temp_dir_prefix: str,
    armored_file_name: str,
    timeout: float,
    owner_uid: int,
    owner_gid: int,
    dearmor_command: Sequence[str] | None = None,
) -> None:
    """Download the signing key of a vendor into the keyring path.

    The key is downloaded into a temporary directory under the given name. Without
    dearmor_command the armored file is the keyring and is placed as downloaded;
    with it, gpg dearmors the downloaded file into the keyring path. The keyring
    is root-owned with the mode of every deployed file.
    """

    keyring_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=temp_dir_prefix) as tmp:
        downloaded = Path(tmp) / armored_file_name
        run_command(download_command(downloaded, url), timeout=timeout)
        if dearmor_command is None:
            keyring_path.write_bytes(downloaded.read_bytes())
        else:
            run_command(
                substituted_command(
                    dearmor_command,
                    {"output": str(keyring_path), "armored": str(downloaded)},
                ),
                timeout=timeout,
            )
    keyring_path.chmod(common_values.LAUNCHER_FILE_MODE)
    apply_owner(keyring_path, owner_uid, owner_gid)


def write_root_file(
    path: Path, content: str, *, owner_uid: int, owner_gid: int
) -> bool:
    """Write one root-owned file when its content differs; True when written."""

    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(common_values.LAUNCHER_FILE_MODE)
    apply_owner(path, owner_uid, owner_gid)
    return True
