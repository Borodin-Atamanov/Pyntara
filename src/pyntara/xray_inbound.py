"""Server half of the panel: the inbound, the client and the profile.

This module owns stages 3 and 5 of the task: the universal VLESS+REALITY
inbound of this machine, the REALITY key pair the panel issues for it,
the single client that inbound serves and the connection profile that is
stored in the runtime vault, so a finished run leaves the node usable as
a server (docs/spec/3x-ui.md).

The client half of the machine is a separate subject: the local proxy
inbound and its routing policy live in pyntara.xray_local_proxy.
"""

from __future__ import annotations

import os

from pyntara import metrics, xray_panel
from pyntara import xui as xui_client
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import proquint_encode, task_data_dir
from pyntara.values import local_vault_setup as local_vault_values
from pyntara.values import three_x_ui_xray_setup as panel_values
from pyntara.xray_facts import _bare_address, _RunFacts, _server_share_address


def _ensure_inbound_security(
    env: dict[str, str],
    inbound: dict[str, object],
    timeout: float,
) -> tuple[bool, tuple[str, ...]]:
    """Ensure the inbound carries the REALITY key pair the panel needs.

    The panel writes pbk into a share link only when the public key sits
    in the nested realitySettings.settings block, so the inbound the task
    owns must carry both halves of the pair. The pair is taken from the
    panel itself: getNewX25519Cert returns both keys and both are written
    back through one inbound update. An inbound that already carries both
    halves is left alone, so a rerun is a no-op, while an inbound that
    lost either half is issued a fresh pair: a public key without its
    private key describes a connection no client can make. Returns
    (changed, warnings): a step that cannot be applied is a warning, never
    an error, so the rest of the task continues.
    """

    fields = panel_values.XRAY_FIELD_KEYS
    stream = inbound.get(fields["stream_settings"])
    if not isinstance(stream, dict):
        return False, ("inbound has no stream settings",)
    reality = stream.get(fields["reality_settings"])
    if not isinstance(reality, dict):
        return False, ("inbound has no REALITY settings",)
    stored = reality.get(fields["settings"])
    has_public = isinstance(stored, dict) and bool(
        stored.get(fields["public_key"])
    )
    has_private = bool(reality.get(fields["private_key"]))
    if has_public and has_private:
        return False, ()
    keypair = xui_client.generate_reality_key(env, timeout)
    if keypair is None:
        return False, ("cannot read the REALITY key pair from the panel",)
    private_key, public_key = keypair
    reality[fields["private_key"]] = private_key
    reality[fields["settings"]] = {
        fields["public_key"]: public_key,
        fields["fingerprint"]: panel_values.REALITY_FINGERPRINT,
    }
    stream[fields["reality_settings"]] = reality
    inbound[fields["stream_settings"]] = stream
    ok, message = xui_client.update_inbound(env, inbound, timeout)
    if not ok:
        return False, (f"inbound update failed: {message}",)
    _log("inbound REALITY key pair issued by the panel and stored")
    return True, ()


def _stage3(
    payload_template: str, timeout: float
) -> TaskResult | None:
    """Run stage 3: create the universal server inbound.

    Reads the panel credentials from install-result.env, searches for an
    existing inbound on the configured port, and creates a VLESS+REALITY
    inbound when none exists. The REALITY keypair is generated through
    the panel API and the keys are appended to the vault entry notes.

    Returns None on success (inbound created or already exists). Returns
    a TaskResult when a non-fatal problem occurs (missing env, unreachable
    panel, vault unavailable), so the caller returns it as a
    done-with-warnings result.
    """

    # Read the credentials the panel generated on first start.
    env, warning = xray_panel._panel_environment_or_warning(timeout)
    if env is None:
        return warning
    _log("stage 3: read credentials from install-result.env")

    # Check if an inbound on the configured port already exists.
    existing = xui_client.find_inbound_by_port(env, panel_values.INBOUND_PORT, timeout)
    if existing is not None:
        _log(f"stage 3: inbound on port {panel_values.INBOUND_PORT} already exists")
        updated, security_warnings = _ensure_inbound_security(
            env, existing, timeout
        )
        if security_warnings:
            return TaskResult(
                success=True, changed=updated, warnings=security_warnings
            )
        if updated:
            return TaskResult(
                success=True, changed=True, message="inbound share data updated"
            )
        return None
    _log(f"stage 3: no inbound on port {panel_values.INBOUND_PORT}, will create")

    # Generate a REALITY keypair through the panel API.
    keypair = xui_client.generate_reality_key(env, timeout)
    if keypair is None:
        return TaskResult(
            success=True,
            changed=False,
            warnings=("failed to generate REALITY keypair: panel may be unreachable",),
        )
    private_key, public_key = keypair
    _log("stage 3: REALITY keypair generated")

    # Build and send the inbound creation payload from the configured
    # template, so the fields and protocol words of the panel API live in
    # task_data/ and not in this module.
    payload = xui_client.build_vless_reality_payload(
        payload_template,
        port=panel_values.INBOUND_PORT,
        remark=panel_values.INBOUND_REMARK,
        dest=panel_values.REALITY_DEST,
        server_names=panel_values.REALITY_SERVER_NAMES,
        private_key=private_key,
        public_key=public_key,
        short_id=panel_values.REALITY_SHORT_ID,
        fingerprint=panel_values.REALITY_FINGERPRINT,
        sniffing_protocols=panel_values.INBOUND_SNIFFING_PROTOCOLS,
    )
    ok, msg = xui_client.create_inbound(env, payload, timeout)
    if not ok:
        _log(f"stage 3: inbound creation failed: {msg}")
        return TaskResult(
            success=True,
            changed=False,
            warnings=(f"inbound creation failed: {msg}",),
        )
    _log(f"stage 3: inbound created ({msg})")

    return TaskResult(success=True, changed=True, message="inbound created")


def _notes_map(notes: str) -> dict[str, str]:
    """Parse the key=value lines of a vault entry into a dict."""

    values: dict[str, str] = {}
    for line in notes.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _connection_notes(
    address: str,
    email: str,
    client_id: str,
    sub_id: str,
    public_key: str,
    private_key: str,
) -> str:
    """The connection profile as the key=value lines of the vault notes.

    The canonical share link lives in the entry url field, so the notes
    carry the parameters a client needs to build its own configuration
    and the keys that describe the server side of the connection.
    """

    lines = [
        f"SERVER_ADDRESS={address}",
        f"SERVER_PORT={panel_values.INBOUND_PORT}",
        f"DEST={panel_values.REALITY_DEST}",
        f"SERVER_NAME={panel_values.REALITY_SERVER_NAMES[0]}",
        f"CLIENT_EMAIL={email}",
        f"CLIENT_ID={client_id}",
        f"SUB_ID={sub_id}",
        f"SHORT_ID={panel_values.REALITY_SHORT_ID}",
        f"FINGERPRINT={panel_values.REALITY_FINGERPRINT}",
        f"REALITY_PUBLIC_KEY={public_key}",
        f"REALITY_PRIVATE_KEY={private_key}",
    ]
    return "\n".join(lines)


def _stage_connection(
    timeout: float,
    facts: _RunFacts,
    *,
    force: bool = False,
) -> TaskResult | None:
    """Ensure the panel has a client and the vault carries its profile.

    Three independent steps, each reported as a warning instead of an
    error: the panel share address is set so rendered links carry a real
    host, one client is ensured with an identity reused from the vault
    entry, and the profile with the canonical share link and the REALITY
    keys is written into that entry. Returns None when nothing changed, a
    TaskResult carrying changed and the warnings otherwise.
    """

    env, warning = xray_panel._panel_environment_or_warning(timeout)
    if env is None:
        return warning
    _log("stage 5: read credentials from install-result.env")

    inbound = xui_client.find_inbound_by_port(env, panel_values.INBOUND_PORT, timeout)
    if inbound is None:
        return TaskResult(
            success=True,
            changed=False,
            warnings=(
                f"no inbound on port {panel_values.INBOUND_PORT}: connection profile not stored",
            ),
        )

    warnings: list[str] = []
    changed = False

    # The share address: the panel renders the link host from it, and only
    # the configured strategy makes the panel use it, so both fields are
    # written together whenever either one differs. Comparing the strategy
    # as well keeps the panel honest when a reset leaves the wanted address
    # behind with the default strategy, a state that would render every
    # link with the host localhost.
    share_address_field = panel_values.XRAY_FIELD_KEYS["share_addr"]
    share_strategy_field = panel_values.XRAY_FIELD_KEYS["share_addr_strategy"]
    address = _server_share_address(inbound, facts)
    if address is None:
        warnings.append(
            "no server address available: panel links keep the default host"
        )
    elif (
        force
        or inbound.get(share_address_field) != address
        or inbound.get(share_strategy_field) != panel_values.SHARE_ADDR_STRATEGY
    ):
        inbound[share_strategy_field] = panel_values.SHARE_ADDR_STRATEGY
        inbound[share_address_field] = address
        ok, message = xui_client.update_inbound(env, inbound, timeout)
        if ok:
            changed = True
            _log(f"panel share address set to {address}")
        else:
            address = None
            warnings.append(f"cannot set the panel share address: {message}")

    # The client identity comes from the vault entry when it is already
    # there, so a rerun reuses the same client instead of adding another.
    kp = metrics.open_runtime_vault()
    stored: dict[str, str] = {}
    if kp is None:
        warnings.append("runtime vault unavailable: connection profile not stored")
    else:
        entry = kp.find_entries(
            title=panel_values.CONNECTION_VAULT_ENTRY_TITLE,
            group=kp.root_group,
            recursive=False,
            first=True,
        )
        if entry is not None:
            stored = _notes_map(entry.notes or "")
    # An identity the panel already serves is adopted when the vault carries
    # none, so a lost vault entry never adds a second client. Extra clients
    # are left alone and named: deleting them is not this task's business,
    # and a warning here would make every later run of the machine look
    # broken until an operator cleans the panel.
    served = _client_records_of_inbound(inbound)
    adopted = not stored.get("CLIENT_EMAIL") and bool(served)
    if len(served) > 1:
        _log(
            f"the inbound serves {len(served)} clients "
            f"({', '.join(_client_emails(served))}): one identity is "
            "reused and the others are left alone"
        )
        if adopted:
            warnings.append(
                f"the inbound serves {len(served)} clients while the vault "
                "carries no client identity: the task adopted "
                f"{_client_emails(served)[0]} from the panel and left "
                "the other clients in place"
            )
    email, client_id, sub_id = _client_identity(stored, served)

    inbound_id = inbound.get("id")
    if not isinstance(inbound_id, int):
        return TaskResult(
            success=True,
            changed=changed,
            warnings=tuple(warnings)
            + ("inbound has no id: connection profile not stored",),
        )
    if xui_client.find_client(env, email, timeout) is None:
        ok, message = xui_client.create_client(
            env, inbound_id, client_id, email, sub_id, timeout
        )
        if not ok:
            return TaskResult(
                success=True,
                changed=changed,
                warnings=tuple(warnings) + (f"cannot create the client: {message}",),
            )
        changed = True
        _log(f"client ensured: {email}")

    links = xui_client.client_links(env, email, timeout)
    link = links[0] if links else ""
    if not link:
        warnings.append("panel returned no share link: connection profile not stored")
    if kp is None or not link:
        return TaskResult(success=True, changed=changed, warnings=tuple(warnings))

    fields = panel_values.XRAY_FIELD_KEYS
    stream = inbound.get(fields["stream_settings"])
    reality_map = (
        stream.get(fields["reality_settings"])
        if isinstance(stream, dict)
        else None
    )
    reality = reality_map if isinstance(reality_map, dict) else {}
    settings_block = reality.get(fields["settings"])
    public_key = ""
    if isinstance(settings_block, dict):
        public_key = str(settings_block.get(fields["public_key"]) or "")
    private_key = str(reality.get(fields["private_key"]) or "")

    notes = _connection_notes(
        _bare_address(address) if address else "",
        email,
        client_id,
        sub_id,
        public_key,
        private_key,
    )
    entry = kp.find_entries(
        title=panel_values.CONNECTION_VAULT_ENTRY_TITLE,
        group=kp.root_group,
        recursive=False,
        first=True,
    )
    if (
        entry is not None
        and (entry.notes or "") == notes
        and (entry.url or "") == link
    ):
        return TaskResult(success=True, changed=changed, warnings=tuple(warnings))
    if entry is not None:
        entry.username = _bare_address(address) if address else ""
        entry.url = link
        entry.notes = notes
    else:
        kp.add_entry(
            kp.root_group,
            panel_values.CONNECTION_VAULT_ENTRY_TITLE,
            _bare_address(address) if address else "",
            "",
            url=link,
            notes=notes,
        )
    kp.save(filename=str(local_vault_values.LOCAL_VAULT_PATH))
    _log(f"connection profile stored in {panel_values.CONNECTION_VAULT_ENTRY_TITLE}")
    return TaskResult(
        success=True,
        changed=True,
        message="connection profile stored",
        warnings=tuple(warnings),
    )


def _read_inbound_payload_template(
    ctx: Context
) -> str:
    """The text of the configured inbound payload template.

    The document lives in task_data/<task>/ of the clone the run started
    from; it holds the field names and the protocol words of the panel
    API, so a panel version that renames a field is answered in the
    template. Reading it is the only step of the task that can fail before
    the panel is touched, so the caller reports an unreadable template as
    a warning of a completed task and never as a traceback.
    """

    return (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / panel_values.INBOUND_PAYLOAD_TEMPLATE_FILE_NAME
    ).read_text(encoding="utf-8")


def _client_records_of_inbound(
    inbound: dict[str, object]
) -> list[dict[str, object]]:
    """The client records the panel stores on one inbound.

    The settings block of an inbound is a JSON document the panel hands
    over as text, and its clients array is where the panel keeps the
    identity of every client it serves. Reading it lets the task reuse an
    identity that already exists instead of adding another client. The
    name of the settings block is a declared value; the name of the
    array is the word the shipped payload template writes.
    """

    settings = xui_client._decoded_json_object(
        inbound.get(panel_values.XRAY_FIELD_KEYS["settings"])
    )
    if settings is None:
        return []
    clients = settings.get("clients")
    if not isinstance(clients, list):
        return []
    return [client for client in clients if isinstance(client, dict)]


def _client_emails(
    clients: list[dict[str, object]]
) -> tuple[str, ...]:
    """The labels of the clients of one inbound, for a message."""

    field = panel_values.PANEL_FIELD_KEYS["email"]
    return tuple(str(client.get(field) or "?") for client in clients)


def _client_identity(
    stored: dict[str, str],
    served: list[dict[str, object]],
) -> tuple[str, str, str]:
    """The email, client id and subscription id of the panel client.

    An identity already stored for this panel wins, because the profile of
    the previous run was built from it. When the vault carries none and
    the panel already serves a client, the identity of that client is
    adopted from the panel: a lost vault entry must not turn into a second
    client on an inbound the task keeps at one, which is the state two
    clients of the same inbound were found in on a live machine. A machine
    with neither generates a fresh identity, and the length of the random
    part of each value is a declared value.
    """

    if not stored.get("CLIENT_EMAIL") and served:
        fields = panel_values.PANEL_FIELD_KEYS
        first = served[0]
        email = str(first.get(fields["email"]) or "")
        client_id = str(first.get(fields["id"]) or "")
        sub_id = str(first.get(fields["sub_id"]) or "")
        if email and client_id:
            _log(
                f"the vault carries no client identity: reusing {email} "
                "from the panel"
            )
            return email, client_id, sub_id
    email = stored.get("CLIENT_EMAIL") or proquint_encode(
        os.urandom(panel_values.RANDOM_USERNAME_BYTES), "-"
    )
    client_id = stored.get("CLIENT_ID") or proquint_encode(
        os.urandom(panel_values.RANDOM_SECRET_BYTES), "-"
    )
    sub_id = stored.get("SUB_ID") or proquint_encode(
        os.urandom(panel_values.RANDOM_SUB_ID_BYTES), ""
    )
    return email, client_id, sub_id
