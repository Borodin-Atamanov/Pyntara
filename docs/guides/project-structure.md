# Project structure

This document defines the target repository layout for Pyntara and explains what each directory and file contains.

## Configuration editing

Many tasks must not overwrite whole files; they must perform targeted line-level edits while preserving unrelated content and comments. The single shared implementation of the line-edit approach lives in src/pyntara/config_edit.py; tasks import its functions instead of copying the logic (docs/guides/project-rules.md section 4).

replace_line_by_string edits text in memory: every line containing the needle or the slide is replaced with the slide, a line containing the stop word is left untouched, a line equal to the slide is never touched, and the slide is appended when nothing matched and add_slide_if_no_needle is true. It returns the new text and whether anything changed.

add_line_to_file ensures a line is present in a file: an exact line is kept, a fuzzy line containing it is normalized to the exact line, a line containing the comment sign is left untouched and the missing line is appended. It returns whether the file changed; a missing file is not created.

The helpers fit files where one setting is one line and the line order does not matter: systemd unit files, fstab, hosts, key = value files. External tools complement them where a line edit cannot express the change: Augeas (augeas-tools, installed by cli_tools) where a format lens exists, comby where no lens exists but the structure is regular, dasel/yq/jq for JSON/YAML/TOML/XML. Structured formats are edited with their parsers, never with line edits: config.toml loads through tomllib in src/pyntara/config/.

## Top-level files

inst.sh — Bootstrap installer: installs dependencies, clones repo, launches Python CLI. See docs/contracts/bootstrap.md.
README.md — Quick start, installation modes, and links to detailed docs.
config/ — Engine configuration and the task catalog, single source of truth for the Python part. One TOML file per top-level section (engine.toml, cli_tools.toml, tasks.toml, ...); the loader joins them in sorted order into one document. See docs/contracts/architecture.md.
hooks/pre-commit — Version bump hook: bumps the patch version before every commit (docs/guides/developer-guide.md, section Version bumping).
.gitignore — Ignore rules for virtualenvs, caches, logs, and runtime task data.

## docs/

contracts/ — Mandatory runtime specifications
spec/ — Functional specification, what the system does
guides/ — How to work with the project

## secrets/

secrets/default.vault — Default/fallback KeePass database for test or recovery scenarios. In git.
secrets/production.vault — Production KeePass database with real secrets. In git.
secrets/default.password — Password for default.vault (well-known test value). In git.
secrets/production.password — Password for production.vault. Not in git (.gitignore).

The layout of both vault files is described in the [vault_structure] table of the config/ directory, the single source of truth for the vault structure (docs/spec/secrets-model.md).
secrets/regenerate_vault_by_config.py — Creates or updates a vault file from the [vault_structure] table of the config/ directory (docs/spec/secrets-model.md).
secrets/read_google_script_credentials.py — Prints the script ID, the deployment ID and the shared auth key of the System Metrics Google Drive web app from the google_script_key entry of a vault (username, the deployment ID embedded in url, and the password field); consumed by task_data/system_metrics_setup/deploy_google_script.sh, which substitutes the key into the __GOOGLE_SCRIPT_KEY__ template placeholder of google_drive_script.js.

## src/pyntara/

src/pyntara/__init__.py — Package version and public exports.
src/pyntara/bump_version.py — Version bumping: reads the version from __init__.py, computes the next patch version and writes it into __init__.py and inst.sh through config_edit.replace_line_by_string. Consumed by hooks/pre-commit.
src/pyntara/pyntara.py — Command entry (check-vault, run) and composition root. The only module that reads the environment.
src/pyntara/config/ — Config.toml loading: Config frozen dataclass, load_config, ConfigError. Split by config section: one module per *_table parser and its dataclass, shared field helpers in _fields.py, whole-config assembly in loader.py, public surface re-exported from the package __init__.
src/pyntara/task_catalog.py — Task catalog logic: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog loaded from the config/ directory.
src/pyntara/models.py — TaskResult dataclass.
src/pyntara/context.py — Context frozen dataclass.
src/pyntara/task_runner.py — Task execution engine: loads task modules by name, runs them in order, collects results.
src/pyntara/utils.py — Shared helpers: run_command subprocess wrapper with timeout and return-code checks, service_is_enabled and service_is_active systemd status queries, proquint_encode and proquint_decode pronounceable encoding of arbitrary bytes (draft-rayner-proquint) with the alphabet and bit layout fixed in the module, plus trim_whitespace, backoff_delay, ensure_root_owner, package and os-release helpers.
src/pyntara/augeas.py — Generic augeas helpers: read, write and sync a drop-in config file through augtool. Used by ssh_daemon_setup and ssh_client_setup.
src/pyntara/config_edit.py — Line-level config editing helpers (section Configuration editing).
src/pyntara/i2pd.py — Shared I2P helpers: decode the .b32.i2p tunnel address from the binary PrivateKeys record. Imported by i2pd_service_setup and i2pd_address.
src/pyntara/i2pd_address.py — Deployed address command: prints the I2P tunnel address from the live keys file or the saved fallback. Runs as `python -m pyntara.i2pd_address`.
src/pyntara/nextdns.py — NextDNS profile selection and endpoint derivation: sha256(hostname) modulo pool size, DoT/DoH endpoint formulas. Imported by nextdns_setup_system_wide and nextdns_profile.
src/pyntara/nextdns_profile.py — Shared vault selection: opens a KeePass group and selects the deterministic profile ID. Imported by nextdns_setup_system_wide.
src/pyntara/ssh.py — Shared SSH helpers: read the sshd listen port from the ssh_daemon_setup directives. Imported by i2pd_service_setup and tor_setup.
src/pyntara/tor.py — Shared Tor helpers: read the onion address from the hidden service hostname file. Imported by tor_setup and tor_address.
src/pyntara/tor_address.py — Deployed address command: prints the Tor onion address from the live hostname file or the saved fallback. Runs as `python -m pyntara.tor_address`.
src/pyntara/yggdrasil.py — Shared Yggdrasil helpers: parse the node self address from yggdrasilctl JSON output. Imported by yggdrasil_service_setup and yggdrasil_address.
src/pyntara/yggdrasil_address.py — Deployed address command: prints the yggdrasil self address from the admin socket or the saved fallback. Runs as `python -m pyntara.yggdrasil_address`.
src/pyntara/metrics.py — Long-running System Metrics service: periodic runtime vault availability check with journal logging (current placeholder, docs/spec/system-metrics.md).
src/pyntara/metrics_ingest.py — Queue ingest: moves spool files into the main_outbox directory. Runs as `python -m pyntara.metrics_ingest`.
src/pyntara/metrics_collect.py — Report collector: runs console commands, waits for network modules, writes the report and commits it. Runs as `python -m pyntara.metrics_collect`.
src/pyntara/metrics_send.py — Queue sender: dispatches entries from main_outbox into channel queues and drains them into delivery endpoints. Runs as part of the system_metrics service.
src/pyntara/metrics_commit.py — Commit command logic: the thin bash script generated by system_metrics_setup delegates to this module for testing.
src/pyntara/tasks/ — One module per task, each exposing task(ctx) -> TaskResult.

Not implemented yet (target modules, see docs/simplified-architecture.md):
src/pyntara/secrets_store.py — Vault loading/decryption and controlled secret access API.
src/pyntara/systemd.py — Creation/update of systemd unit files and timers.

### src/pyntara/tasks/

One module per task, each exposing task(ctx) -> TaskResult. Task names come from the [[tasks]] section of the config/ directory, the single source of truth; the module list is not repeated here so renames in the config cannot leave stale names behind.

## Config section map

Each TOML file in config/ has a corresponding parser module in src/pyntara/config/ and a frozen dataclass. The parser is wired in loader.py and the dataclass is exported from config/__init__.py. Tasks receive the whole Config through Context and access their section by name.

engine -> config/engine.py -> EngineConfig -> all tasks via Context
cli_tools -> config/cli_tools.py -> CliToolsConfig -> cli_tools
add_extra_repos -> config/add_extra_repos.py -> AddExtraReposConfig -> add_extra_repos
hostname -> config/hostname.py -> HostnameConfig -> hostname
swapfile_service_install -> config/swapfile_service_install.py -> SwapfileServiceInstallConfig -> swapfile_service_install
zram_service -> config/zram_service.py -> ZramServiceConfig -> zram_service
zswap_service -> config/zswap_service.py -> ZswapServiceConfig -> zswap_service
dnscrypt_setup -> config/dnscrypt_setup.py -> DnscryptSetupConfig -> dnscrypt_setup
dnsproxy_setup -> config/dnsproxy_setup.py -> DnsproxySetupConfig -> dnsproxy_setup
i2pd_service_setup -> config/i2pd_service_setup.py -> I2pdServiceSetupConfig -> i2pd_service_setup
yggdrasil_service_setup -> config/yggdrasil_service_setup.py -> YggdrasilServiceSetupConfig -> yggdrasil_service_setup
tor_setup -> config/tor_setup.py -> TorSetupConfig -> tor_setup
ssh_daemon_setup -> config/ssh.py -> SshDaemonSetupConfig -> ssh_daemon_setup
ssh_client_setup -> config/ssh.py -> SshClientSetupConfig -> ssh_client_setup
nextdns_setup_system_wide -> config/nextdns_setup_system_wide.py -> NextdnsSetupSystemWideConfig -> nextdns_setup_system_wide
system_metrics_setup -> config/system_metrics_setup.py -> SystemMetricsSetupConfig -> system_metrics_setup
vault_structure -> config/vault.py -> VaultStructureConfig -> local_vault_setup, nextdns_setup_system_wide
local_vault_setup -> config/vault.py -> LocalVaultSetupConfig -> local_vault_setup
tasks -> config/tasks.py -> tuple[TaskConfig, ...] -> task_catalog.py

## Public API surface

Shared helpers that tasks import instead of reimplementing. When you need a capability, check this list first.

Module              Public functions
utils.py            run_command, package_is_installed, install_package_once,
                    read_os_release, os_family_is_debian, dpkg_architecture,
                    service_is_enabled, service_is_active, ensure_root_owner,
                    proquint_encode, proquint_decode, trim_whitespace,
                    backoff_delay

config_edit.py      replace_line_by_string, add_line_to_file,
                    sync_directives_by_key, sync_toml_root_directive

augeas.py           parse_augtool_print, sync_dropin, read_dropin,
                    dropin_exists, remove_dropin

nextdns.py          select_profile_id, profile_endpoints

nextdns_profile.py  select_profile_from_vault

i2pd.py             b32_address

yggdrasil.py        self_address_from_output

tor.py              onion_address_from_hostname_file

ssh.py              ssh_port_from_directives

## Adding a new config section

1. Create config/<name>.toml with the values and comments.
2. Create src/pyntara/config/<name>.py with a frozen dataclass and a _<name>_table parser function.
3. Add the dataclass field to the Config class in loader.py.
4. Wire the parser in load_config() in loader.py.
5. Export the dataclass from config/__init__.py.
