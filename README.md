# Pyntara 0.2.167

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

Pyntara turns a fresh Kubuntu installation into a fully configured workstation or server
in one command. It installs packages, configures ZRAM and swap, sets up SSH, DNS and
anonymity services (dnsproxy, i2pd, yggdrasil, Tor), tunes the desktop environment, and
enables encrypted System Metrics reporting. All tasks are idempotent — safe to rerun. A
single bootstrap script downloads the repo and launches the Python provisioning engine.

## Start

The main run asks for the production vault password on every invocation via read -s and
passes it only to the installer process; the password is never stored in the shell
environment:

```bash
inst="$(mktemp /tmp/pyntara.XXXXXXXXX)" \
&& curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused \
-o "$inst" https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh \
&& sudo --preserve-env=PYNTARA_INSTALL_MODE,PYNTARA_TASKS,PYNTARA_FORCE_TASKS,PYNTARA_SKIP_APT_UPDATE \
bash -c 'read -r -s -p "Enter production vault password: " p && PYNTARA_VAULT_PASSWORD="$p" bash "$1"' _ "$inst"
```

The installer runs non-interactively and never asks the user anything. The vault source is
auto-detected from the password: production when it opens production.vault, default when it
matches default.password. Without a password, or with a password that matches no vault, the
installer shows a short countdown notice and falls back to the default vault.

Optional environment variables can be added inside the sudo bash -c block, separated by
spaces before the script invocation:

PYNTARA_VAULT_SOURCE — production or default. When omitted, the source is auto-detected from the password.

PYNTARA_INSTALL_MODE — minimal, server or desktop. When omitted, the mode is auto-detected from the system (desktop or server).

PYNTARA_TASKS — space-separated task names. When omitted, the default task set of the chosen mode is used.

PYNTARA_FORCE_TASKS — space-separated task names that must rerun even when the target state
is already reached. When omitted, no task is forced. The keyword all forces every task of
the resolved run set. Task names and the keyword are case-insensitive. Invalid names are
reported with a countdown notice and ignored.

PYNTARA_SKIP_APT_UPDATE — 1, true or yes skips the apt index refresh that inst.sh,
add_extra_repos and cli_tools run before package operations. Use for test or offline runs;
omit it in real provisioning so packages resolve from a fresh index.

The developer run keeps the password in the shell environment: it asks once, exports
PYNTARA_VAULT_PASSWORD, and skips the prompt on later runs in the same terminal when the
variable is already set to a non-empty value. PYNTARA_SKIP_APT_UPDATE=1 sits in the script
invocation prefix, so it reaches the installer and the engine; a flag joined with && would
only set a shell variable and never reach the installer:

```bash
if [[ -z "${PYNTARA_VAULT_PASSWORD:-}" ]]; then
    read -r -s -p "Enter production vault password: " PYNTARA_VAULT_PASSWORD
    echo
fi
export PYNTARA_VAULT_PASSWORD
inst="$(mktemp /tmp/pyntara.XXXXXXXXX)"
curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused \
-o "$inst" https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh
sudo --preserve-env=PYNTARA_VAULT_PASSWORD,PYNTARA_INSTALL_MODE,PYNTARA_TASKS,PYNTARA_FORCE_TASKS,PYNTARA_SKIP_APT_UPDATE \
bash -c 'PYNTARA_SKIP_APT_UPDATE=1 bash "$1"' _ "$inst"
```

Engine values and the task catalog live in the config/ directory at the repository root,
one TOML file per top-level section, joined by the loader into a single document
([Configuration](docs/contracts/architecture.md#configuration)).

The interactive installer variant does not work and its development is stopped.

## Documentation index

AI-Agent rules: [AGENTS.md](AGENTS.md)

Contracts — mandatory runtime specifications, must not be violated. Only MUST assertions
testable in code:

[docs/contracts/architecture.md](docs/contracts/architecture.md) — runtime boundaries, composition root, Context, resilience rule  
[docs/contracts/bootstrap.md](docs/contracts/bootstrap.md) — bootstrap installer contract for inst.sh  
[docs/contracts/task-model.md](docs/contracts/task-model.md) — task model, idempotency contract, catalog and dependencies

Spec — functional specification, what the system does and how. Design rationale, formulas,
parameters. May reference contracts but never repeat them:

[docs/spec/install-modes.md](docs/spec/install-modes.md) — minimal/server/desktop modes, auto-detection, task and force selection  
[docs/spec/secrets-model.md](docs/spec/secrets-model.md) — KeePass vaults, passwords, PYNTARA_VAULT_PASSWORD, fallback  
[docs/spec/system-metrics.md](docs/spec/system-metrics.md) — encrypted PDF System Metrics, queues, retries, Telegram and Google Drive  
[docs/spec/nextdns-profile.md](docs/spec/nextdns-profile.md) — NextDNS profile selection and the profile ID file read by dnsproxy and System Metrics  
[docs/spec/dnsproxy-setup.md](docs/spec/dnsproxy-setup.md) — dnsproxy system-wide resolver, NextDNS encrypted upstreams, cache and fallback servers  
[docs/spec/i2pd-service.md](docs/spec/i2pd-service.md) — i2pd service install from GitHub releases, version and asset selection, download trust  
[docs/spec/yggdrasil-service.md](docs/spec/yggdrasil-service.md) — yggdrasil service install from GitHub releases, version and asset selection, download trust  
[docs/spec/3x-ui.md](docs/spec/3x-ui.md) — 3x-ui Xray panel install via the official installer, version gate and credential boundary  
[docs/spec/tor-service.md](docs/spec/tor-service.md) — Tor install from the Ubuntu archive, SSH onion service, address file and client side  
[docs/spec/ssh-daemon-setup.md](docs/spec/ssh-daemon-setup.md) — SSH server install, drop-in configuration, pre-generated key deployment  
[docs/spec/ssh-client-setup.md](docs/spec/ssh-client-setup.md) — system-wide SSH client defaults, drop-in configuration  
[docs/spec/port-forwarding-setup.md](docs/spec/port-forwarding-setup.md) — Auto Port Forwarding service, reverse ssh tunnels to the vault port-forwarding servers  
[docs/spec/users-and-host.md](docs/spec/users-and-host.md) — hostname, ZRAM, zswap, swapfile  
[docs/spec/kde-keyboard-setup.md](docs/spec/kde-keyboard-setup.md) — KDE keyboard layouts, switch options, the layout indicator and per-layout hotkeys, applied via kwriteconfig6 and the kglobalaccel daemon  
[docs/spec/kde-settings.md](docs/spec/kde-settings.md) — KDE dark color scheme, dark global theme, NumLock, touchpad and Wayland virtual keyboard, applied via the plasma-apply tools and kwriteconfig6
[docs/spec/imagemagick-setup.md](docs/spec/imagemagick-setup.md) — ImageMagick install from the Ubuntu archive plus the tuned security policy, idempotent and without a version chase  
[docs/spec/ffmpeg-setup.md](docs/spec/ffmpeg-setup.md) — ffmpeg install from the Ubuntu archive, idempotent and without a version chase  
[docs/spec/rustdesk-setup.md](docs/spec/rustdesk-setup.md) — RustDesk remote desktop client install from GitHub releases, public server registration, per-machine password and the network report ID  
Guides — how to work with the project:

[docs/guides/project-structure.md](docs/guides/project-structure.md) — repository layout, file responsibilities, config editing tools  
[docs/guides/project-rules.md](docs/guides/project-rules.md) — code conventions: output policy, datetime format, engineering standards  
[docs/guides/developer-guide.md](docs/guides/developer-guide.md) — quick start, running the test suite (uv run pytest), linting, type checking, CI, commit workflow, task best practices  
[docs/guides/planning-procedure.md](docs/guides/planning-procedure.md) — mandatory planning procedure for tasks that require a plan

Architecture decisions:

[docs/simplified-architecture.md](docs/simplified-architecture.md) — approved simplification rationale, resilience rule

Plans:

[docs/TODO.md](docs/TODO.md) — planned future work, ideas for new tasks
