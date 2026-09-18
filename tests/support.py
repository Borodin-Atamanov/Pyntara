"""Shared test factories and fakes for the engine test suite.

The Context shape repeats in every test module, so it is defined once
here. FakeProc replaces the identical subprocess stub classes that were
copied per file. Domain-specific fakes (sysfs mirrors, disk usage) stay
in their own test modules.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from pyntara.context import Context

# Root of the clone the tests run from: the tests directory sits one level
# under it. The suite names it itself, exactly as the composition root does.
REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def augtool_fake_run(command: list[str], input_: str | None) -> FakeProc:
    """Simulate augtool --noautoload over the real drop-in file.

    The fake implements the subset of augeas the tasks use: a manual
    load entry, load, print, set, rm and save. The tree is keyed by
    augeas path without [index] suffixes; nested nodes (the Host block
    of ssh_config) keep their parent-child paths, and save writes
    indented lines for them. The lens is not needed, because the file
    layout is derived from the indentation.
    """

    script = input_ or ""
    incl: str | None = None
    tree: dict[str, str] = {}
    out_lines: list[str] = []
    base = ""
    last_top: str | None = None
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("set "):
            parts = line.split(" ", 2)
            path, value = parts[1], parts[2].strip('"')
            if path.startswith("/augeas/load/"):
                if path.endswith("/incl"):
                    incl = value
                    base = f"/files{incl}"
                continue
            path = path.replace("[last()]", "")
            if path.endswith("/#comment"):
                existing = sorted(
                    node for node in tree if node.startswith(base + "/#comment")
                )
                if existing:
                    tree[existing[0]] = value
                else:
                    tree[path] = value
            else:
                tree[path] = value
        elif line.startswith("rm "):
            path = line.split(" ", 1)[1]
            for node in [
                node for node in tree if node == path or node.startswith(path + "/")
            ]:
                del tree[node]
            out_lines.append(f"rm : {path}")
        elif line == "load":
            tree = {}
            last_top = None
            if incl:
                file_path = Path(incl)
                if file_path.is_file():
                    comment_count = 0
                    for text in file_path.read_text(encoding="utf-8").splitlines():
                        if not text.strip():
                            continue
                        indented = text != text.lstrip()
                        stripped = text.strip()
                        if stripped.startswith("#"):
                            comment_count += 1
                            node = (
                                f"{base}/#comment"
                                if comment_count == 1
                                else f"{base}/#comment[{comment_count}]"
                            )
                            tree[node] = stripped[1:].strip()
                            last_top = node
                        else:
                            key, sep, value = stripped.partition(" ")
                            value = value.strip() if sep else ""
                            if indented and last_top is not None:
                                node = f"{last_top}/{key}"
                            else:
                                node = f"{base}/{key}"
                                last_top = node
                            tree[node] = value
        elif line.startswith("print "):
            path = line.split(" ", 1)[1]
            out_lines.append(path)
            for node, value in tree.items():
                if node.startswith(path + "/"):
                    out_lines.append(f'{node} = "{value}"')
        elif line == "save":
            if incl:
                file_path = Path(incl)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                content: list[str] = []
                for node, value in tree.items():
                    suffix = node[len(base) :].lstrip("/")
                    if "/" in suffix:
                        continue
                    label = suffix.split("[", 1)[0]
                    if label.startswith("#"):
                        content.append(f"# {value}")
                        continue
                    children = [
                        (child, child_value)
                        for child, child_value in tree.items()
                        if child.startswith(node + "/")
                    ]
                    if children:
                        content.append(f"{label} {value}")
                        for child, child_value in children:
                            child_label = (
                                child[len(node) :].lstrip("/").split("[", 1)[0]
                            )
                            content.append(f"\t{child_label} {child_value}")
                    else:
                        content.append(f"{label} {value}")
                file_path.write_text("\n".join(content) + "\n", encoding="utf-8")
            out_lines.append("Saved 1 file(s)")
    return FakeProc(0, "\n".join(out_lines) + "\n")


def make_context(
    *,
    install_mode: str = "minimal",
    vault_password: str | None = None,
    vault_source: str | None = None,
    force_tasks: frozenset[str] = frozenset(),
    repo_root: Path = REPO_ROOT,
    task_data_root: Path = Path("/tmp"),
    skip_apt_update: bool = False,
    task_name: str = "",
) -> Context:
    """Context with the safe defaults the engine fills in a real run.

    repo_root defaults to the clone the tests run from, so a test that
    reads a real template keeps working; a test whose task renders a
    fixture passes its own directory here instead of monkeypatching a
    module constant.

    task_name is the name of the task under test. The runner fills it in
    a real run, so a test that exercises force mode or a task-data
    template passes the catalog name of the module it calls.
    """

    return Context(
        install_mode=install_mode,
        vault_password=vault_password,
        vault_source=vault_source,
        force_tasks=force_tasks,
        repo_root=repo_root,
        task_data_root=task_data_root,
        skip_apt_update=skip_apt_update,
        task_name=task_name,
    )


# Fixture PrivateKeys record shared by the i2pd tests (the decoder, the
# address command and the task): the 387-byte IdentityEx (256-byte
# encryption key, 128-byte signing key, 3-byte certificate) with a KEY
# certificate carrying the 4-byte extended block of the signing and
# crypto key types, followed by private material. The expected address
# is the unpadded lowercase base32 of the SHA-256 of the IdentityEx,
# computed independently from the same parts.
I2PD_KEYS_IDENTITY_SIZE = 387
I2PD_KEYS_CERTIFICATE_TYPE_KEY = 5
I2PD_KEYS_EXTENDED_BYTES = b"\x00\x07\x00\x04"  # signing type 7, crypto type 4


def i2pd_keys_file_bytes() -> bytes:
    """The fixture PrivateKeys record for the i2pd tests."""

    identity = bytearray(I2PD_KEYS_IDENTITY_SIZE)
    identity[I2PD_KEYS_IDENTITY_SIZE - 3] = I2PD_KEYS_CERTIFICATE_TYPE_KEY
    identity[I2PD_KEYS_IDENTITY_SIZE - 2] = 0
    identity[I2PD_KEYS_IDENTITY_SIZE - 1] = len(I2PD_KEYS_EXTENDED_BYTES)
    return bytes(identity) + I2PD_KEYS_EXTENDED_BYTES + b"private material"


def i2pd_keys_b32_address() -> str:
    """The expected .b32.i2p address of the fixture keys record."""

    identity_len = I2PD_KEYS_IDENTITY_SIZE + len(I2PD_KEYS_EXTENDED_BYTES)
    digest = hashlib.sha256(i2pd_keys_file_bytes()[:identity_len]).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{encoded}.b32.i2p"
