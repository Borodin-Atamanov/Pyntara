"""Shared pytest configuration.

Journal forwarding must never reach the real system journal during unit
tests, so PYNTARA_JOURNAL_IDENTIFIER is set to an empty value here, before
any test module imports the application. logger reads the variable lazily,
so an empty value disables systemd-cat for the whole test run. The journal
integration tests in test_logger.py override the variable locally with
their own identifiers.

KeePass databases carry Argon2 with 64 MiB and 14 iterations, which costs
about half a second per open or save. The vault tests create and reopen
dozens of databases, so the derivation dominates the suite. create_database
is replaced with a wrapper that builds every test database with the minimum
Argon2 configuration (1 iteration, 8 MiB): the database stays fully
functional and the derivation drops to milliseconds. The wrapper copies the
committed fixture tests/fixtures/template.kdbx, so no worker pays the
original expensive derivation. The patch is applied at module import, so
every test module and the standalone secrets scripts pick it up through
their own `from pykeepass import create_database` import.

The fixture is a KeePass 4 database with the minimum Argon2 configuration
and the template password. Regenerate it when pykeepass changes its vault
format:

    python -c "from pathlib import Path; from pykeepass.pykeepass import \
        PyKeePass, create_database; kp = create_database(\
        'tests/fixtures/template.kdbx', password='pyntara-template-password'); \
        d = kp.kdbx.header.value.dynamic_header.kdf_parameters.data.dict; \
        d['I'].value = 1; d['M'].value = 8 * 1024 * 1024; d['P'].value = 1; \
        kp.kdbx.header.pop('data', None); [kp.kdbx.body.pop(n, None) for n in \
        ('transformed_key', 'master_key', 'sha256', 'cred_check')]; kp.save()"
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pykeepass as _pykeepass
from pykeepass.exceptions import CredentialsError

os.environ["PYNTARA_JOURNAL_IDENTIFIER"] = ""

_original_create_database = _pykeepass.create_database

# The template vault used for every test database. The committed fixture is
# preferred; when it is missing, a fresh template is built lazily once per
# worker process in a temporary directory.
_TEMPLATE_PASSWORD = "pyntara-template-password"
_TEMPLATE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "template.kdbx"
_template_dir: str | None = None
_template_path: Path | None = None


def _cheapen_kdf(kp: _pykeepass.PyKeePass) -> None:
    """Rewrite an open vault to the minimum Argon2 configuration.

    The KDF parameters live in a variant dictionary inside the serialized
    header. Mutating the parsed values and dropping the cached serialized
    bytes makes the next save rebuild the header from the new values; the
    computed key fields are dropped as well, so the save re-derives them
    with the cheap parameters instead of reusing the cached key. When the
    pykeepass internals change shape, the fallback keeps the expensive but
    correct database: tests stay green, only slower.
    """

    try:
        kdf_parameters = (
            kp.kdbx.header.value.dynamic_header.kdf_parameters.data.dict
        )
        kdf_parameters["I"].value = 1
        kdf_parameters["M"].value = 8 * 1024 * 1024
        kdf_parameters["P"].value = 1
        kp.kdbx.header.pop("data", None)
        for name in ("transformed_key", "master_key", "sha256", "cred_check"):
            kp.kdbx.body.pop(name, None)
        kp.save()
    except (KeyError, AttributeError, TypeError):
        # pykeepass changed its header layout; leave the vault untouched.
        return


def _fixture_usable() -> bool:
    """True when the committed template fixture opens with the template password.

    A corrupt fixture falls back to building a fresh template instead of
    failing every vault test with a confusing credential error. A fixture in
    an unknown format raises outside the handled exceptions and fails
    loudly, which points at the regeneration command in the module docstring.
    """

    if not _TEMPLATE_FIXTURE.is_file():
        return False
    try:
        _pykeepass.PyKeePass(str(_TEMPLATE_FIXTURE), password=_TEMPLATE_PASSWORD)
    except (CredentialsError, OSError):
        return False
    return True


def _ensure_template() -> Path:
    """Return the cheap-KDF template vault to copy for every create call."""

    if _fixture_usable():
        return _TEMPLATE_FIXTURE
    global _template_dir, _template_path
    if _template_path is None:
        _template_dir = tempfile.mkdtemp(prefix="pyntara-tests-")
        _template_path = Path(_template_dir) / "template.kdbx"
        atexit.register(shutil.rmtree, _template_dir, ignore_errors=True)
        kp = _original_create_database(
            str(_template_path), password=_TEMPLATE_PASSWORD
        )
        _cheapen_kdf(kp)
    return _template_path


def _create_database_fast(
    filename: str | None,
    password: str | None = None,
    keyfile: object = None,
    transformed_key: bytes | None = None,
) -> _pykeepass.PyKeePass:
    """create_database with a cheap KDF: copy the template, re-encrypt.

    Copying the cheap template and re-saving it under the requested password
    costs one cheap derivation instead of the original expensive one. The
    returned instance is fully functional, so callers that add entries and
    save afterwards keep working unchanged. Calls that use keyfiles or a
    precomputed key fall back to the original implementation, which the test
    suite never exercises.
    """

    if filename is None or keyfile is not None or transformed_key is not None:
        return _original_create_database(
            filename,
            password=password,
            keyfile=keyfile,
            transformed_key=transformed_key,
        )
    target = Path(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_ensure_template(), target)
    kp = _pykeepass.PyKeePass(str(target), password=_TEMPLATE_PASSWORD)
    kp.password = password
    kp.save()
    return kp


_pykeepass.create_database = _create_database_fast
