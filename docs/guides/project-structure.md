# Project structure

This document defines the target repository layout for Pyntara and explains what each directory and file contains.

## Configuration editing

Many tasks must not overwrite whole files; they must perform targeted line-level edits while preserving unrelated content and comments. The single shared implementation of the line-edit approach lives in src/pyntara/config_edit.py; tasks import its functions instead of copying the logic ([General engineering requirements](project-rules.md#general-engineering-requirements)).

replace_line_by_string edits text in memory: every line containing the needle or the slide is replaced with the slide, a line containing the stop word is left untouched, a line equal to the slide is never touched, and the slide is appended when nothing matched and add_slide_if_no_needle is true. It returns the new text and whether anything changed.

add_line_to_file ensures a line is present in a file: an exact line is kept, a fuzzy line containing it is normalized to the exact line, a line containing the comment sign is left untouched and the missing line is appended. It returns whether the file changed; a missing file is not created.

The helpers fit files where one setting is one line and the line order does not matter: systemd unit files, fstab, hosts, key = value files. External tools complement them where a line edit cannot express the change: Augeas (augeas-tools, installed by the tasks that use augeas) where a format lens exists, comby where no lens exists but the structure is regular, dasel/yq/jq for JSON/YAML/TOML/XML. Structured formats are edited with their parsers, never with line edits.

## Top-level files

inst.sh — Bootstrap installer: installs dependencies, clones repo, launches Python CLI. See docs/contracts/bootstrap.md.  
pyntara.sh — Launcher: downloads inst.sh from the raw branch and runs it as root, with every run parameter held in the file itself and the inherited environment ignored. See docs/contracts/bootstrap.md.  
README.md — Quick start, installation modes, and links to detailed docs.  
hooks/pre-commit — Build version hook: bumps the single build version carrier before every commit, so the number grows per commit without a merge conflict (docs/guides/developer-guide.md, [Version bumping](developer-guide.md#version-bumping)).
hooks/land_version_commit.sh — Landing step: bumps the version on the branch tip, mirrors it into inst.sh and README.md, verifies the three carriers and records one commit before the push to main (docs/guides/developer-guide.md, [Version bumping](developer-guide.md#version-bumping)).
scripts/check_gates.sh — Every gate of docs/guides/developer-guide.md in one command: the linting, both type checks, the test suite and the five bash suites. Run by hand before a landing and by .github/workflows/checks.yml in the pipeline. Its --fast argument checks only the touched python files and the test modules that match them by name.
.github/workflows/checks.yml — Pipeline: runs scripts/check_gates.sh on every push and every pull request, with Python 3.14 and uv that never downloads an interpreter.
.gitattributes — Marks src/pyntara/_version.py merge=union, so a version conflict resolves into two lines the version tool normalizes instead of a stopped merge (docs/guides/developer-guide.md, [Version bumping](developer-guide.md#version-bumping)).  
.gitignore — Ignore rules for virtualenvs, caches, logs, and runtime task data.

## docs/

contracts/ — Mandatory runtime specifications  
spec/ — Functional specification, what the system does  
guides/ — How to work with the project

## secrets/

The four secret files (default/production vaults and their passwords) are listed in [Secrets files](../contracts/bootstrap.md#secrets-files); their structure is described in [Secrets model](../spec/secrets-model.md).

secrets/regenerate_vault_by_config.py — Creates or updates a vault file from the declared vault entries of src/pyntara/values/vault_structure.py (docs/spec/secrets-model.md).  
secrets/read_google_script_credentials.py — Prepares the System Metrics Google Drive web app deploy: reads the google_script_key entry of both vaults (the production vault supplies the script ID in username and the deployment ID embedded in url, every vault supplies an auth key in password), renders task_data/system_metrics_setup/google_drive_script.js with the __GOOGLE_SCRIPT_KEYS__ placeholder replaced by the JSON array of those keys, and prints the two identifiers for task_data/system_metrics_setup/deploy_google_script.sh, which pushes the rendered file to the Apps Script project.

## src/pyntara/

src/pyntara/__init__.py — Package docstring and the version re-export from pyntara._version.  
src/pyntara/_version.py — Build version carrier: the single line __version__, rewritten by the pre-commit hook on every commit and marked merge=union in .gitattributes, so the number grows per commit without a merge conflict.
src/pyntara/bump_version.py — Version bumping: reads the build carrier, computes the next patch version and writes the carrier, the PYNTARA_VERSION line of inst.sh and the README title through config_edit.replace_line_by_string, then verifies that every carrier carries the new number and raises ValueError naming the ones that do not. Consumed by hooks/pre-commit (--build-only) and by the landing step hooks/land_version_commit.sh, which commits the carrier list this module reports.  
src/pyntara/pyntara.py — Command entry (check-vault, run) and composition root. The only module that reads the environment.  
src/pyntara/task_catalog.py — Task catalog logic: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog from src/pyntara/values/tasks.py.  
src/pyntara/models.py — TaskResult dataclass.  
src/pyntara/context.py — Context frozen dataclass.  
src/pyntara/task_runner.py — Task execution engine: loads task modules by name, runs them in order, collects results.  
src/pyntara/utils.py — Shared helpers: run_command subprocess wrapper with timeout and return-code checks, service_is_enabled and service_is_active systemd status queries, proquint_encode and proquint_decode pronounceable encoding of arbitrary bytes (draft-rayner-proquint) with the alphabet and bit layout fixed in the module, plus trim_whitespace, backoff_delay, apply_owner, package and os-release helpers.  
src/pyntara/augeas.py — Generic augeas helpers: read, write and sync a drop-in config file through augtool. Used by ssh_daemon_setup and ssh_client_setup.  
src/pyntara/config_edit.py — Line-level config editing helpers (see [Configuration editing](#configuration-editing)).  
src/pyntara/i2pd.py — Shared I2P helpers: decode the .b32.i2p tunnel address from the binary PrivateKeys record. Imported by i2pd_service_setup and i2pd_address.  
src/pyntara/i2pd_address.py — Deployed address command: prints one JSON record with the I2P tunnel address and the ssh command that reaches the SSH daemon through the tunnel, from the live keys file or the saved fallback. Runs as `python -m pyntara.i2pd_address`.  
src/pyntara/network_addresses.py — Deployed address command: prints one JSON record per address of one family, each with its interface, its scope and the ssh command that connects to it. Runs as `python -m pyntara.network_addresses FAMILY`.  
src/pyntara/public_address_report.py — Deployed command: prints one JSON record per public address reported by the configured echo services, each with its ssh command, and a reason record for a family without an answer. Runs as `python -m pyntara.public_address_report`.  
src/pyntara/country_report.py — Deployed command: prints the country the configured services see, with the answers and the decision word. Runs as `python -m pyntara.country_report`.  
src/pyntara/ssh_access.py — Shared construction of the ssh access command of an address: the verbose client, the always written port, the absent user and the SOCKS ProxyCommand of an anonymity channel. Imported by every address command, so the form of the reported command lives in one place.  
src/pyntara/nextdns.py — NextDNS profile selection: sha256(hostname) modulo pool size and the profile ID shape validation. Imported by nextdns_profile.  
src/pyntara/nextdns_profile.py — Shared vault selection: opens a KeePass group and selects the deterministic profile ID. Imported by nextdns_setup_system_wide.
src/pyntara/forwarding_ports.py — The shared deterministic port chain of a machine: desired_port is the port of one name, derived from sha256 of the name mapped into the declared range, and candidate_ports yields the candidates of the chain in the order they are tried, so the reverse tunnel service and the router service can never disagree about the number that names a machine (docs/spec/port-forwarding-setup.md, docs/spec/upnp-forwarding-setup.md). Imported by port_forwarding and upnp_forwarding.
src/pyntara/port_forwarding.py — Long-running Auto Port Forwarding service: keeps reverse ssh tunnels to the vault port-forwarding servers, records the remote port it holds per server and local port in the state file and triggers a fresh System Metrics collection on every port change, so the network report carries the current ports. Runs as `python -m pyntara.port_forwarding`.
src/pyntara/public_address.py — Shared address discovery: queries the configured echo services in one parallel curl call, waits for every answer and returns the unique IPv4 and IPv6 addresses with the repeats merged; also reads the interface addresses (local_addresses) and the address of the default route (default_route_address) (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup and upnp.
src/pyntara/upnp.py — Shared UPnP port-forwarding helpers over the external upnpc client: reads the router address, lists the existing mappings with the description of every rule, renders the configured description into the ownership mark of this machine with mapping_description, asks the router for a mapping, leaves a rule of another machine alone and reads the result back; forward_inbound_port runs the whole attempt and returns the router address with the scope that says whether the internet reaches it, so an address behind a provider NAT is kept instead of lost. The module installs nothing and only runs the program it is given (docs/spec/3x-ui.md, docs/spec/upnp-forwarding-setup.md). Imported by three_x_ui_xray_setup and upnp_forwarding.
src/pyntara/xui.py — Panel REST and CLI client of the 3x-ui panel: the CSRF login, the Bearer-token calls for inbounds, clients, share links, settings and the Xray template, the routing test and the core status, the balancer status, the outbound subscriptions and the panel environment of install-result.env. Every path, field name and protocol word comes from the config, so a panel release that renames an endpoint is answered there and not in the code (docs/spec/3x-ui.md). Imported by the Xray stage modules (xray_panel, xray_inbound, xray_certificate, xray_local_proxy), by their shared machinery (xray_client) and by sotavpn_setup.
src/pyntara/routing_policy.py — The routing document of the local proxy as pure functions: the parsed vless profile, the outbound of the remote server, the rules of the policy, the observatory and the least-ping balancer of the pool, and tag_matches_selector. It takes values and a template and returns the updated template plus whether anything really differs, so a rerun writes nothing (docs/spec/3x-ui.md). Imported by xray_client and xray_local_proxy.
src/pyntara/xray_client.py — Shared machinery of the client half of the panel: machine_policy builds the policy of this machine from its own answers, ensure_local_proxy_inbound reconciles the proxy inbound, apply_policy_to_template writes the outbounds and rules, and the route checks ask the running core about every destination class and accept a pool member or the fallback of the pool as an answer (docs/spec/3x-ui.md). Imported by xray_local_proxy.
src/pyntara/xray_facts.py — Run facts of the panel task, read once per run: the public addresses the echo services report, the addresses of the interfaces, the address of the UPnP router, the UPnP client package check and the host a client link must carry (own address, forwarded address, yggdrasil address, local address, stored share address). Imported by three_x_ui_xray_setup and by xray_certificate, xray_inbound and xray_local_proxy.
src/pyntara/xray_panel.py — The 3x-ui panel as a service: the version gate and the official installer with its environment, the proquint credentials, the fixed panel port, install-result.env, the credential takeover, the vault entry of stage 2 and the subscription paths of the panel settings (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup and by xray_certificate.
src/pyntara/xray_certificate.py — HTTPS of the panel: whether the HTTP-01 challenge can reach this machine, the acme.sh issue path, the openssl path of a self-signed certificate, the upgrade to a trusted certificate and stage 4 itself (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup.
src/pyntara/xray_inbound.py — Server half of the panel: the universal VLESS+REALITY inbound with the key pair the panel issues, the payload template reader, the single client of that inbound and the connection profile stored in the runtime vault, which are stages 3 and 5 (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup.
src/pyntara/xray_local_proxy.py — Client half of the panel: the local proxy inbound of this machine, its routing policy, the pool of remote exits the remote classes leave through and the path checks that prove the exit, which are stages 6 and 7 (docs/spec/3x-ui.md). Imported by three_x_ui_xray_setup.
src/pyntara/port_forwarding_state.py — Deployed command that prints one JSON record per server and forwarded local port from the state file, each with the ssh command that reaches this machine through it; the System Metrics collector runs it as the port_forwarding network module. Runs as `python -m pyntara.port_forwarding_state`.
src/pyntara/upnp_forwarding.py — Deployed oneshot service that asks the home router through UPnP to publish the SSH port of this machine: it derives the external port from the hostname with the shared deterministic chain of pyntara.forwarding_ports, tries the next candidate when another rule holds the port, never touches a rule of another machine, and wakes the System Metrics collector when it changed the router. Runs as `python -m pyntara.upnp_forwarding`.
src/pyntara/upnp_forwarding_state.py — Deployed command that reads the rules of the home router live and prints one JSON record per rule of this machine with the router address, the published port, the scope of the address and the ssh command; the System Metrics collector runs it as the upnp network module. Runs as `python -m pyntara.upnp_forwarding_state`.
src/pyntara/ssh.py — Shared SSH helpers: read the sshd listen port from the ssh_daemon_setup directives. Imported by i2pd_service_setup and tor_setup.
src/pyntara/tor.py — Shared Tor helpers: read the onion address from the hidden service hostname file. Imported by tor_setup and tor_address.  
src/pyntara/tor_address.py — Deployed address command: prints one JSON record with the Tor onion address and the ssh command that reaches the SSH daemon through the onion service, from the live hostname file or the saved fallback. Runs as `python -m pyntara.tor_address`.  
src/pyntara/yggdrasil.py — Shared Yggdrasil helpers: parse the node self address from yggdrasilctl JSON output. Imported by yggdrasil_service_setup and yggdrasil_address.  
src/pyntara/yggdrasil_address.py — Deployed address command: prints one JSON record with the yggdrasil self address and the ssh command that reaches the SSH daemon over the overlay, from the admin socket or the saved fallback. Runs as `python -m pyntara.yggdrasil_address` and reads its values from the values package.  
src/pyntara/metrics.py — Long-running System Metrics service: periodic runtime vault availability check with journal logging (current placeholder, docs/spec/system-metrics.md).  
src/pyntara/metrics_ingest.py — Queue ingest: moves spool files into the main_outbox directory. Runs as `python -m pyntara.metrics_ingest`.  
src/pyntara/metrics_collect.py — Report collector: runs console commands, waits for network modules, writes the report and commits it. Runs as `python -m pyntara.metrics_collect`.  
src/pyntara/metrics_send.py — Queue sender: dispatches entries from main_outbox into channel queues and drains them into delivery endpoints. Runs as part of the system_metrics service.  
src/pyntara/metrics_commit.py — Commit command logic: the thin bash script generated by system_metrics_setup delegates to this module for testing.  
src/pyntara/tasks/ — One module per task, each exposing task(ctx) -> TaskResult.

Modules planned but not implemented yet are listed in [What is next](../simplified-architecture.md#what-is-next-separate-changes).

### src/pyntara/tasks/

One module per task, each exposing task(ctx) -> TaskResult. Task names come from the catalog in src/pyntara/values/tasks.py, the single source of truth; the module list is not repeated here so renames in the catalog cannot leave stale names behind.

## Section map

Every section has exactly one module in src/pyntara/values/, and a reader imports that module as an alias and reads the declared names through it. Which value types a module holds, and which never become a value, is the Configuration section of [Architecture](../contracts/architecture.md#configuration).

engine -> src/pyntara/values/engine.py -> READ_VALUE_NAMES -> every module that reads a value
common -> src/pyntara/values/common.py -> READ_VALUE_NAMES -> the tasks that read a machine-wide path or account
tasks -> src/pyntara/values/tasks.py -> TaskSpec -> task_catalog.py
cli_tools_lite_setup -> src/pyntara/values/cli_tools_lite_setup.py -> READ_VALUE_NAMES -> the task
cli_tools_heavy_setup -> src/pyntara/values/cli_tools_heavy_setup.py -> READ_VALUE_NAMES -> the task
chrome_setup -> src/pyntara/values/chrome_setup.py -> READ_VALUE_NAMES -> the task
add_extra_repos -> src/pyntara/values/add_extra_repos.py -> READ_VALUE_NAMES -> the task
hostname -> src/pyntara/values/hostname.py -> READ_VALUE_NAMES -> the task
swapfile_service_install -> src/pyntara/values/swapfile_service_install.py -> READ_VALUE_NAMES -> the task
zram_service -> src/pyntara/values/zram_service.py -> READ_VALUE_NAMES -> the task
zswap_service -> src/pyntara/values/zswap_service.py -> READ_VALUE_NAMES -> the task
dnsproxy_setup -> src/pyntara/values/dnsproxy_setup.py -> READ_VALUE_NAMES -> the task
i2pd_service_setup -> src/pyntara/values/i2pd_service_setup.py -> READ_VALUE_NAMES -> the task and the deployed address command
yggdrasil_service_setup -> src/pyntara/values/yggdrasil_service_setup.py -> READ_VALUE_NAMES -> the task and the deployed address command
three_x_ui_xray_setup -> src/pyntara/values/three_x_ui_xray_setup.py -> READ_VALUE_NAMES -> the task and the deployed report commands
sotavpn_setup -> src/pyntara/values/sotavpn_setup.py -> READ_VALUE_NAMES -> the task
tor_setup -> src/pyntara/values/tor_setup.py -> READ_VALUE_NAMES -> the task and the deployed address command
ssh_daemon_setup -> src/pyntara/values/ssh_daemon_setup.py -> READ_VALUE_NAMES -> the task and the shared SSH port reader
ssh_client_setup -> src/pyntara/values/ssh_client_setup.py -> READ_VALUE_NAMES -> the task
nextdns_setup_system_wide -> src/pyntara/values/nextdns_setup_system_wide.py -> READ_VALUE_NAMES -> the task
port_forwarding_setup -> src/pyntara/values/port_forwarding_setup.py -> READ_VALUE_NAMES -> the task, the deployed service and the state command
upnp_forwarding_setup -> src/pyntara/values/upnp_forwarding_setup.py -> READ_VALUE_NAMES -> the task, the deployed service and the state command
playwright_setup -> src/pyntara/values/playwright_setup.py -> READ_VALUE_NAMES -> the task
system_metrics_setup -> src/pyntara/values/system_metrics_setup.py -> READ_VALUE_NAMES -> the deployed service, the collector, the ingest, the commit command and the tasks
vault_structure -> src/pyntara/values/vault_structure.py -> READ_VALUE_NAMES -> local_vault_setup and nextdns_setup_system_wide
local_vault_setup -> src/pyntara/values/local_vault_setup.py -> READ_VALUE_NAMES -> the task and every reader of the runtime vault
rustdesk_setup -> src/pyntara/values/rustdesk_setup.py -> READ_VALUE_NAMES -> the task
valualinux_setup -> src/pyntara/values/vocalinux_setup.py -> READ_VALUE_NAMES -> the task

## Public API surface

Shared helpers that tasks import instead of reimplementing. When you need a capability, check this list first.

Module              Public functions
utils.py            run_command, package_is_installed, install_package_once,
                    read_os_release, os_family_is_debian, dpkg_architecture,
                    service_is_enabled, service_is_active, apply_owner,
                    proquint_encode, proquint_decode, trim_whitespace,
                    backoff_delay

config_edit.py      replace_line_by_string, add_line_to_file,
                    sync_directives_by_key

augeas.py           parse_augtool_print, sync_dropin, read_dropin,
                    dropin_exists, remove_dropin

nextdns.py          profile_id_is_valid, select_profile_id

nextdns_profile.py  select_profile_from_vault

i2pd.py             b32_address

yggdrasil.py        self_address_from_output

tor.py              onion_address_from_hostname_file

ssh.py              ssh_port_from_directives

## Adding a value to an existing section

This is the common case: the section already has its values module, so a value touches that module, its readers and the tests.

Confirm that the value belongs in a values module at all: the Configuration section of [Architecture](../contracts/architecture.md#configuration) says which types are values and which stay in code.  
Write the constant in src/pyntara/values/<section>.py with a comment that explains what it is, and add its name to READ_VALUE_NAMES of that module. The rule of tests/value_checks.py that fits its type is applied to it by the values guard; a value that needs a new kind of check gets it there.  
Read it where it is used through the alias the module is imported under, never by copying the literal.  
Describe the value in the Parameters section of the matching document in docs/spec/.

Nothing breaks on a machine when a step is forgotten, because the run works with the values it has and every task reports what it missed. The test suite is what catches the omission, during development.

## Adding a new section

Create src/pyntara/values/<name>.py with the declared values, their comments and READ_VALUE_NAMES.  
Add the module name to VALUES_MODULE_NAMES in tests/test_values.py, so the values guard applies the rules to it.  
Import the module in the task with the alias the import guard expects, or add the reason to EXTRA_VALUE_RULES when the section needs a rule of its own.

A new section needs no parser and no loader: the values are Python constants the task imports, so a value that is not declared is an ImportError the task reports in plain words, and the remaining tasks still run.

## Value guards

tests/value_checks.py holds the rules a declared value must satisfy: the file mode, the non-empty text, the non-negative integer, the non-empty text tuple, the vault entry title and the real package name checks.  
tests/test_values.py applies every rule to every declared value and refuses a value that is read without the module alias, so a literal copied into a task body fails the suite.  
tests/test_values_softness.py proves that an unimportable values module and an undeclared name both cost their own task only, while the run continues.

