"""Writing the runtime secret vault of the machine.

Several places write the runtime vault: the local_vault_setup task builds it
and merges the structure entries of the source vault into it, and the tasks
that store machine specific secrets add their entries to it (rustdesk_setup,
the panel of three_x_ui_xray_setup and the connection profile of its inbound).
Every writer goes through this module, so the file mode of the secret database
lives in one place.

The mode needs a place of its own because of a measured behaviour: a save
through the KeePass library writes the database as a new file and gives it the
umask of the process, so the mode the values declare (LOCAL_VAULT_FILE_MODE,
0640) was silently replaced by 0664, or 0644 on a machine with the usual umask,
after any task stored a secret. The secrets of the machine then became readable
by every user of the machine (measured on a fresh run of 2026-09-25, where the
vault ended world readable with fifteen entries, among them the RustDesk
password, the panel credentials and the telemetry password). Both functions
here apply the declared mode right after the write.
"""

from __future__ import annotations

import os

from pykeepass import PyKeePass

from pyntara.values import local_vault_setup as values


def save_runtime_vault(kp: PyKeePass) -> None:
    """Save the runtime vault in place and restore its declared mode.

    The vault is edited in place by the tasks that add one entry to it, so the
    write keeps the entries that are already there; only the mode the save
    dropped is put back.
    """

    kp.save(filename=str(values.LOCAL_VAULT_PATH))
    os.chmod(values.LOCAL_VAULT_PATH, values.LOCAL_VAULT_FILE_MODE)


def write_runtime_vault(kp: PyKeePass, password: str) -> None:
    """Re-encrypt an opened source vault and write it as the runtime vault.

    The copy carries the password of the runtime vault, so the source password
    never opens it, and it goes to a temporary file next to the target that is
    moved onto it afterwards: an interrupted write then leaves the previous
    file or the complete new one, never a truncated secret database where the
    machine reads it. The directory and the file carry the declared modes.
    """

    target = values.LOCAL_VAULT_PATH
    kp.password = password
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, values.SECRETS_DIR_MODE)
    temporary_path = target.with_name(
        target.name + values.LOCAL_VAULT_TEMPORARY_SUFFIX
    )
    kp.save(filename=str(temporary_path))
    os.chmod(temporary_path, values.LOCAL_VAULT_FILE_MODE)
    os.replace(temporary_path, target)
