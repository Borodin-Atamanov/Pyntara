# Project structure

This document defines the target repository layout for Pyntara and explains what each directory and file contains.

## Configuration editing

Many tasks must not overwrite whole files; they must perform targeted line-level edits while preserving unrelated content and comments. The single shared implementation of the line-edit approach lives in src/pyntara/config_edit.py; tasks import its functions instead of copying the logic ([General engineering requirements](project-rules.md#general-engineering-requirements)).

replace_line_by_string edits text in memory: every line containing the needle or the slide is replaced with the slide, a line containing the stop word is left untouched, a line equal to the slide is never touched, and the slide is appended when nothing matched and add_slide_if_no_needle is true. It returns the new text and whether anything changed.

add_line_to_file ensures a line is present in a file: an exact line is kept, a fuzzy line containing it is normalized to the exact line, a line containing the comment sign is left untouched and the missing line is appended. It returns whether the file changed; a missing file is not created.

The helpers fit files where one setting is one line and the line order does not matter: systemd unit files, fstab, hosts, key = value files. External tools complement them where a line edit cannot express the change: Augeas (augeas-tools, installed by the tasks that use augeas) where a format lens exists, comby where no lens exists but the structure is regular, dasel/yq/jq for JSON/YAML/TOML/XML. Structured formats are edited with their parsers, never with line edits: config.toml loads through tomllib in src/pyntara/config/.

## Top-level files

inst.sh — Bootstrap installer: installs dependencies, clones repo, launches Python CLI. See docs/contracts/bootstrap.md.  
README.md — Quick start, installation modes, and links to detailed docs.  
config/ — Engine configuration and the task catalog, single source of truth for the Python part. One TOML file per top-level section (engine.toml, cli_tools.toml, tasks.toml, ...); the loader joins them in sorted order into one document. See docs/contracts/architecture.md.  
hooks/pre-commit — Version bump hook: bumps the patch version before every commit (docs/guides/developer-guide.md, [Version bumping](developer-guide.md#version-bumping)).  
.gitignore — Ignore rules for virtualenvs, caches, logs, and runtime task data.

## docs/

contracts/ — Mandatory runtime specifications  
spec/ — Functional specification, what the system does  
guides/ — How to work with the project

## secrets/

The four secret files (default/production vaults and their passwords) are listed in [Secrets files](../contracts/bootstrap.md#secrets-files); their structure is described in [Secrets model](../spec/secrets-model.md).

secrets/regenerate_vault_by_config.py — Creates or updates a vault file from the [vault_structure] table of the config/ directory (docs/spec/secrets-model.md).  
secrets/read_google_script_credentials.py — Prints the script ID, the deployment ID and the shared auth key of the System Metrics Google Drive web app from the google_script_key entry of a vault (username, the deployment ID embedded in url, and the password field); consumed by task_data/system_metrics_setup/deploy_google_script.sh, which substitutes the key into the __GOOGLE_SCRIPT_KEY__ template placeholder of google_drive_script.js.

## src/pyntara/

src/pyntara/__init__.py — Package version and public exports.  
src/pyntara/bump_version.py — Version bumping: reads the version from __init__.py, computes the next patch version and writes it into __init__.py and inst.sh through config_edit.replace_line_by_string. Consumed by hooks/pre-commit.  
src/pyntara/pyntara.py — Command entry (check-vault, run) and composition root. The only module that reads the environment.  
src/pyntara/config/ — Config.toml reading: the Config frozen dataclass, load_config and the runtime reader, one module per section holding its frozen dataclass, the vocabulary constants in _fields.py, the public surface re-exported from the package __init__. The reader takes every value as it is and never fails; no rule of the config is checked here, the checks live in tests/config_checks.py.  
src/pyntara/task_catalog.py — Task catalog logic: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog loaded from the config/ directory.  
src/pyntara/models.py — TaskResult dataclass.  
src/pyntara/context.py — Context frozen dataclass.  
src/pyntara/task_runner.py — Task execution engine: loads task modules by name, runs them in order, collects results.  
src/pyntara/utils.py — Shared helpers: run_command subprocess wrapper with timeout and return-code checks, service_is_enabled and service_is_active systemd status queries, proquint_encode and proquint_decode pronounceable encoding of arbitrary bytes (draft-rayner-proquint) with the alphabet and bit layout fixed in the module, plus trim_whitespace, backoff_delay, ensure_root_owner, package and os-release helpers.  
src/pyntara/augeas.py — Generic augeas helpers: read, write and sync a drop-in config file through augtool. Used by ssh_daemon_setup and ssh_client_setup.  
src/pyntara/config_edit.py — Line-level config editing helpers (see [Configuration editing](#configuration-editing)).  
src/pyntara/i2pd.py — Shared I2P helpers: decode the .b32.i2p tunnel address from the binary PrivateKeys record. Imported by i2pd_service_setup and i2pd_address.  
src/pyntara/i2pd_address.py — Deployed address command: prints the I2P tunnel address from the live keys file or the saved fallback. Runs as `python -m pyntara.i2pd_address`.  
src/pyntara/nextdns.py — NextDNS profile selection: sha256(hostname) modulo pool size and the profile ID shape validation. Imported by nextdns_profile.  
src/pyntara/nextdns_profile.py — Shared vault selection: opens a KeePass group and selects the deterministic profile ID. Imported by nextdns_setup_system_wide.
src/pyntara/port_forwarding.py — Long-running Auto Port Forwarding service: keeps reverse ssh tunnels to the vault port-forwarding servers, records the granted remote ports in the state file and triggers a fresh System Metrics collection on every port change, so the network report carries the current ports. Runs as `python -m pyntara.port_forwarding`.
src/pyntara/public_address.py — Shared address discovery: queries the configured echo services in one parallel curl call, waits for every answer and returns the unique IPv4 and IPv6 addresses with the repeats merged; also reads the interface addresses (local_addresses) and the address of the default route (default_route_address) (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup and upnp.
src/pyntara/upnp.py — Shared UPnP port-forwarding helpers over the external upnpc client: reads the router address, lists the existing mappings, asks the router for a mapping and reads the result back; forward_inbound_port runs the whole attempt and returns the router address only when it can work. The module installs nothing and only runs the program it is given (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup.
src/pyntara/port_forwarding_state.py — Deployed command that prints the assigned remote ports from the state file; the System Metrics collector runs it as the port_forwarding network module. Runs as `python -m pyntara.port_forwarding_state`.
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

Modules planned but not implemented yet are listed in [What is next](../simplified-architecture.md#what-is-next-separate-changes).

### src/pyntara/tasks/

One module per task, each exposing task(ctx) -> TaskResult. Task names come from the [[tasks]] section of the config/ directory, the single source of truth; the module list is not repeated here so renames in the config cannot leave stale names behind.

## Config section map

Each TOML file in config/ has a corresponding module in src/pyntara/config/ with a frozen dataclass, read by the runtime reader through the field names of that dataclass. The checks of a section live in tests/config_checks.py. Tasks receive the whole Config through Context and access their section by name.

engine -> config/engine.py -> EngineConfig -> all tasks via Context  
cli_tools -> config/cli_tools.py -> CliToolsConfig -> cli_tools  
chrome_setup -> config/chrome_setup.py -> ChromeSetupConfig -> chrome_setup  
add_extra_repos -> config/add_extra_repos.py -> AddExtraReposConfig -> add_extra_repos  
hostname -> config/hostname.py -> HostnameConfig -> hostname  
swapfile_service_install -> config/swapfile_service_install.py -> SwapfileServiceInstallConfig -> swapfile_service_install  
zram_service -> config/zram_service.py -> ZramServiceConfig -> zram_service  
zswap_service -> config/zswap_service.py -> ZswapServiceConfig -> zswap_service  
dnsproxy_setup -> config/dnsproxy_setup.py -> DnsproxySetupConfig -> dnsproxy_setup  
i2pd_service_setup -> config/i2pd_service_setup.py -> I2pdServiceSetupConfig -> i2pd_service_setup  
yggdrasil_service_setup -> config/yggdrasil_service_setup.py -> YggdrasilServiceSetupConfig -> yggdrasil_service_setup  
three_x_ui_xray_setup -> config/three_x_ui_xray_setup.py -> ThreeXuiXraySetupConfig -> three_x_ui_xray_setup  
tor_setup -> config/tor_setup.py -> TorSetupConfig -> tor_setup  
ssh_daemon_setup -> config/ssh.py -> SshDaemonSetupConfig -> ssh_daemon_setup  
ssh_client_setup -> config/ssh.py -> SshClientSetupConfig -> ssh_client_setup  
nextdns_setup_system_wide -> config/nextdns_setup_system_wide.py -> NextdnsSetupSystemWideConfig -> nextdns_setup_system_wide
port_forwarding_setup -> config/port_forwarding_setup.py -> PortForwardingSetupConfig -> port_forwarding_setup
playwright_setup -> config/playwright_setup.py -> PlaywrightSetupConfig -> playwright_setup
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

nextdns.py          profile_id_is_valid, select_profile_id

nextdns_profile.py  select_profile_from_vault

i2pd.py             b32_address

yggdrasil.py        self_address_from_output

tor.py              onion_address_from_hostname_file

ssh.py              ssh_port_from_directives

## Adding a value to an existing section

This is the common case: the section already exists, so a value touches the section file, its dataclass and the checks.

Add the key with a comment to the section file in config/ (config/<section>.toml).  
Add the field to the frozen dataclass in src/pyntara/config/<section>.py. The runtime reader takes the value of the key with the same name, so nothing else in the package changes and loader.py is never touched.  
Add the check of the new value to tests/config_checks.py next to the other checks of that section, and add the key to both test documents: the shared document in tests/config_helpers.py, which the section tests parse, and VALID_TOML in tests/test_config.py, which the end-to-end cases parse. The factory in tests/support.py needs no edit at all: every section it builds derives from the shared document.  
Describe the value in the Parameters section of the matching document in docs/spec/.

Nothing breaks on a machine when a step is forgotten, because the run reads what is there and invents no value. The test suite is what catches the omission, during development.

## Adding a new config section

Create config/<name>.toml with the values and comments.  
Create src/pyntara/config/<name>.py with a frozen dataclass.  
Add the dataclass field to the Config class in loader.py.  
Export the dataclass from config/__init__.py.

A new section needs no parser and no change to the reader: the runtime reader builds every section from the field names of its dataclass. The checks of the new section go to tests/config_checks.py, and the key set has to be mirrored in both test documents, because the coverage guard fails while the test copies and the repository config disagree.

## Config coverage guards

tests/config_checks.py holds the strict checks of the config: the types, the allowed sets, the ranges, the cross-checks between sections and the task catalog rules. They are the checks that used to run inside the package; nothing in the package checks anything now.

tests/test_config_coverage.py applies those checks to the repository config and compares the forms of the configuration: the repository config, the two test copies (the shared document in tests/config_helpers.py and VALID_TOML in tests/test_config.py) and the Config the checks build from each of them.

The guards are: the shipped config passes every check, every section of the repository config has a Config field, every Config field has a section, every key of a section is read by its check, every config file contributes a table, a section field is either a key or a recorded derived field, each test copy mirrors the sections and keys of the repository config except the keys the checks document as optional, and the factory config passes the same checks as any other.
