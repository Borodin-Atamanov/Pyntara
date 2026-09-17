"""Values of the vault structure: the layout of every KeePass database.

The structure is the single source of truth for the KeePass layout
(docs/spec/secrets-model.md). Both secrets/default.vault and
secrets/production.vault follow it, and any tooling that creates or inspects them
reads this module. The structure is flat: every entry lives directly in the root
group, the title alone identifies the entry, and titles are verbose so the
purpose is clear without opening the vault. The keys of an entry map one-to-one
to the KeePass entry field names; the notes of an entry explain what is stored
there, who consumes it and why.

Two optional subgroups are part of the structure: NextDNS and
port_forwarding_servers. NextDNS carries the per-profile NextDNS accounts, one
entry per profile. The subgroups are data, not structure: the regeneration
tooling creates the group, fills each with its seed entries on creation (only
port_forwarding_servers has seeds) and never fills or deletes entries
afterwards, so the accounts and addresses survive every regeneration run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VaultEntry:
    """One entry of the vault structure.

    title names the KeePass entry; notes carries the explanatory text that the
    regeneration tooling stores in the notes field of the entry.
    generated_password, when set, asks the regeneration tooling to generate the
    password when it creates the entry, in the format "proquint-N".
    """

    title: str
    notes: str
    generated_password: str | None = None


@dataclass(frozen=True)
class VaultGroupSeed:
    """One seed entry of a group.

    title names the KeePass entry the regeneration tooling creates inside the
    group when it creates the group; url carries the data value (for example a
    port-forwarding server address) and notes carries the explanatory text. Seed
    entries are the default content of a data group, so a freshly created vault
    mirrors the structure before the real data is maintained directly in the
    database.
    """

    title: str
    url: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class VaultGroup:
    """One data subgroup of the vault structure.

    title names the KeePass group, notes explains what it carries. seed_entries
    are the entries the regeneration tooling creates inside the group when it
    creates the group, so a fresh vault starts as a faithful mirror; once the
    group exists, the tooling never touches its entries.
    """

    title: str
    notes: str
    seed_entries: tuple[VaultGroupSeed, ...] = ()


ENTRIES: tuple[VaultEntry, ...] = (
    VaultEntry(
        title="pyntara_local_vault_password",
        notes="Password that protects the runtime secret vault at /var/lib/pyntara/secrets/pyntara.vault on the target machine. Read by the local_vault_setup task and written to /etc/pyntara/pass, so the source vault password never opens the runtime vault. The default vault carries a well-known test value mirroring its well-known vault password.",
    ),
    VaultEntry(
        title="google_script_key",
        notes="Credentials of the System Metrics Google Drive web app. The username field carries the Apps Script project script ID, the url field carries the web app deployment endpoint (from which the deployment ID is derived), the password field carries the auth key of that vault, and the deployed script carries the keys of every vault in its ALLOWED_KEYS list, so the telemetry of the machines provisioned from either vault is accepted. Consumed by the Google script deploy helper and by the System Metrics client. The default vault carries well-known test values mirroring its well-known vault password.",
    ),
    VaultEntry(
        title="telemetry_password",
        generated_password="proquint-4",
        notes="Password that encrypts the System Metrics PDF reports. Read by the encrypted PDF generation when it produces a report. The regeneration tooling creates the entry with a password of 4 proquint words joined by dashes; the production vault carries a password of 4 proquint words joined by spaces, created once by hand after generation, so the two vaults differ both in separator and in value.",
    ),
    VaultEntry(
        title="three_x_ui_credentials",
        notes="Credentials of the 3x-ui Xray panel on this machine. The username field carries the panel admin username, the password field carries the panel admin password, the url field carries the panel base URL (http://127.0.0.1:PORT/BASE_PATH). Additional values are stored in the notes field as key=value lines: XUI_PANEL_PORT, XUI_WEB_BASE_PATH, XUI_API_TOKEN, XUI_DB_TYPE. Consumed by the three_x_ui_xray_setup task stage 2, which reads the credentials from /etc/x-ui/install-result.env and writes them into this entry on first run.",
    ),
    VaultEntry(
        title="xray_connection",
        notes="Connection profile of the Xray server on this machine, written by the three_x_ui_xray_setup task. The url field carries the canonical vless share link; the notes field carries the profile as key=value lines: SERVER_ADDRESS, SERVER_PORT, DEST, SERVER_NAME, CLIENT_EMAIL, CLIENT_ID, SUB_ID, REALITY_PUBLIC_KEY, REALITY_PRIVATE_KEY, SHORT_ID, FINGERPRINT. Consumed by the future client task that reads a server address and a vless link from the production vault.",
    ),
    VaultEntry(
        title="xray_client_profile",
        notes="Connection profile of the remote Xray server that a machine connects to as a client. The url field carries the canonical vless share link of that server and is the single source of truth the client reads; the username field repeats the server address for the operator. Filled by the operator from the connection profile that three_x_ui_xray_setup writes on the server machine, so one server profile serves every machine. Read by the future xray_client_setup task from the vault the run opened: an absent entry or an empty url means no profile is configured, and the task then configures no proxy and reports a warning.",
    ),
    VaultEntry(
        title="sotavpn_uuid",
        notes="Access key of the paid Sota Connect account, a UUID, filled by the operator by hand from the account page or the Sota bot. The sotavpn_setup task reads it from the source vault, production first, and configures the Sotavpn pool only when the password is present and not empty; an absent entry or an empty password means the pool is off for this run. The password of a freshly regenerated default vault stays empty, so a runtime vault built from it mirrors the structure while the pool stays off.",
    ),
    VaultEntry(
        title="ssh_passphase_for_port_forwarding",
        generated_password="proquint-7",
        notes="Passphrase of the port-forwarding private key id_ed25519_pf deployed by the ssh_daemon_setup task. Read by the auto_port_forwarding service from the runtime vault to unlock the key in a dedicated ssh-agent. The entry exists in every vault: the regeneration tooling generates a fresh password of 7 proquint words joined by dashes on creation, so the production secret is never copied and the default vault carries a passphrase that unlocks no deployed key.",
    ),
    VaultEntry(
        title="rustdesk_password",
        notes="Permanent RustDesk access credentials of this machine. The password field carries the permanent password, filled by the rustdesk_setup task as 6 random proquint words joined by spaces; the username field carries the machine RustDesk ID read from rustdesk --get-id. The task generates and writes both values on first run and on force mode and applies the password through rustdesk --password, so every machine of the fleet gets its own credential. Consumed by the rustdesk_setup task; the operator reads the pair from the runtime vault backup to connect by the machine ID.",
    ),
)

GROUPS: tuple[VaultGroup, ...] = (
    VaultGroup(
        title="NextDNS",
        notes="NextDNS profile accounts, one entry per profile. The username field holds the 6-hex profile ID, the title pairs the ID with a proquint name, other fields are unused. The group is data: tooling creates it but never fills or deletes entries, so the accounts survive every vault regeneration. Consumed by the nextdns_setup_system_wide task, which derives one profile per machine hostname.",
    ),
    VaultGroup(
        title="port_forwarding_servers",
        notes="Port-forwarding server addresses, one entry per server. The url field holds the address (ipv4, ipv6 or a url), other fields are unused. The group is data: tooling creates it and fills it with its seed entries on creation, then never edits them, so the addresses survive every vault regeneration. The default vault carries the seed entry with a well-known test address, so a runtime vault derived from it mirrors the production structure while connecting to nothing real. Consumed by the auto_port_forwarding service.",
        seed_entries=(
            VaultGroupSeed(
                title="Server 001",
                url="200:a804:881c:d5d8:6d4e:afab:e158:371",
                notes="Well-known test address of the default vault, mirrors the production server entry without pointing at a real server.",
            ),
        ),
    ),
)

# The names the tooling reads. The list lives next to the values it names, and
# the maintenance script and the values guards read it from here.
READ_VALUE_NAMES: tuple[str, ...] = ("ENTRIES", "GROUPS")
