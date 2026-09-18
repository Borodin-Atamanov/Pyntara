# Pyntara 0.3.577

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
&& curl --fail --location --connect-timeout 60 --retry 17 --retry-delay 3 --retry-all-errors --retry-max-time 7777 --retry-connrefused \
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
is already reached. When omitted, no task is forced. The keyword that forces every task of
the resolved run set is `force_all_keyword` of the engine values module (`all` by
default). Task names and that keyword are compared without case. Invalid names are
reported with a countdown notice and ignored.

PYNTARA_SKIP_APT_UPDATE — 1, true or yes skips the apt index refresh that inst.sh,
add_extra_repos and cli_tools run before package operations. The answers that mean true are
`environment_flag_true_values` of the engine values module (1, true and yes by default), compared
without case. Use it for test or offline runs;
omit it in real provisioning so packages resolve from a fresh index.

The developer run asks for the production vault password once and keeps it in a root-only
file under /dev/shm, so repeated runs on the same machine do not ask again until the next
reboot clears the shared memory. A non-empty PYNTARA_VAULT_PASSWORD already in the
environment wins; otherwise the run reads the cached password, and only when the cache is
empty does it ask interactively and write the answer to the cache. To force a new prompt
after a password change, delete /dev/shm/pyntara/temp_pass or reboot.
A commented PYNTARA_TASKS line inside the command names a single task for quick reruns;
uncomment it to run only that task instead of the whole default set.
PYNTARA_SKIP_APT_UPDATE=1 sits in the script invocation prefix, so it reaches the
installer and the engine; a flag joined with && would only set a shell variable and never
reach the installer:

```bash
sudo --preserve-env=PYNTARA_VAULT_PASSWORD,PYNTARA_INSTALL_MODE,PYNTARA_TASKS,PYNTARA_FORCE_TASKS,PYNTARA_SKIP_APT_UPDATE bash -c '
if [[ -z "${PYNTARA_VAULT_PASSWORD:-}" ]]; then
    pass_file=/dev/shm/pyntara/temp_pass
    if [[ -s "$pass_file" ]]; then
        PYNTARA_VAULT_PASSWORD="$(cat "$pass_file")"
    else
        read -r -s -p "Enter production vault password: " PYNTARA_VAULT_PASSWORD
        echo
        install -d -m 0700 "$(dirname "$pass_file")"
        printf "%s" "$PYNTARA_VAULT_PASSWORD" > "$pass_file"
        chmod 0600 "$pass_file"
    fi
    export PYNTARA_VAULT_PASSWORD
fi
inst="$(mktemp /tmp/pyntara.XXXXXXXXX)"
curl --fail --location --connect-timeout 60 --retry 17 --retry-delay 3 --retry-all-errors --retry-max-time 7777 --retry-connrefused \
-o "$inst" https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh
# Uncomment to run one task instead of the whole default set, for example:
# export PYNTARA_TASKS="system_metrics_setup"
PYNTARA_SKIP_APT_UPDATE=1 bash "$inst"
'
```

Values live in Python modules under src/pyntara/values/, one module per task, and
a task reads the values of its own module, so a value is never written in two
places. The remaining sections still live in the config/ directory at the
repository root, one TOML file per top-level section, joined by the loader into a
single document, and that directory is being retired section by section
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

[docs/spec/config-content.md](docs/spec/config-content.md) — what the config holds, type by type, the closed list of exceptions and the rule that everything else is a config value  
[docs/spec/install-modes.md](docs/spec/install-modes.md) — minimal/server/desktop modes, auto-detection, task and force selection  
[docs/spec/secrets-model.md](docs/spec/secrets-model.md) — KeePass vaults, passwords, PYNTARA_VAULT_PASSWORD, fallback  
[docs/spec/system-metrics.md](docs/spec/system-metrics.md) — encrypted PDF System Metrics, queues, retries, Telegram and Google Drive  
[docs/spec/nextdns-profile.md](docs/spec/nextdns-profile.md) — NextDNS profile selection and the profile ID file read by dnsproxy and System Metrics  
[docs/spec/dnsproxy-setup.md](docs/spec/dnsproxy-setup.md) — dnsproxy system-wide resolver, NextDNS encrypted upstreams, cache and fallback servers  
[docs/spec/i2pd-service.md](docs/spec/i2pd-service.md) — i2pd service install from GitHub releases, version and asset selection, download trust  
[docs/spec/yggdrasil-service.md](docs/spec/yggdrasil-service.md) — yggdrasil service install from GitHub releases, version and asset selection, download trust  
[docs/spec/3x-ui.md](docs/spec/3x-ui.md) — 3x-ui Xray panel install via the official installer, version gate, credential boundary, server inbound, the local proxy client with its routing policy and the pool of remote exits the remote classes leave through  
[docs/spec/sotavpn-setup.md](docs/spec/sotavpn-setup.md) — Sotavpn bridge for the paid account and the panel outbound subscription that fills the pool of the local proxy  
[docs/spec/tor-service.md](docs/spec/tor-service.md) — Tor install from the Ubuntu archive, SSH onion service, address file and client side  
[docs/spec/ssh-daemon-setup.md](docs/spec/ssh-daemon-setup.md) — SSH server install, drop-in configuration, pre-generated key deployment  
[docs/spec/ssh-client-setup.md](docs/spec/ssh-client-setup.md) — system-wide SSH client defaults, drop-in configuration  
[docs/spec/port-forwarding-setup.md](docs/spec/port-forwarding-setup.md) — Auto Port Forwarding service, reverse ssh tunnels to the vault port-forwarding servers  
[docs/spec/upnp-forwarding-setup.md](docs/spec/upnp-forwarding-setup.md) — router port forwarding through UPnP, the published SSH port and its record in the network report  
[docs/spec/users-and-host.md](docs/spec/users-and-host.md) — hostname, ZRAM, zswap, swapfile  
[docs/spec/kde-keyboard-setup.md](docs/spec/kde-keyboard-setup.md) — KDE keyboard layouts, switch options, the layout indicator and per-layout hotkeys, applied via kwriteconfig6 and the kglobalaccel daemon  
[docs/spec/kde-settings.md](docs/spec/kde-settings.md) — KDE dark color scheme, dark global theme, NumLock, touchpad and Wayland virtual keyboard, applied via the plasma-apply tools and kwriteconfig6
[docs/spec/vocalinux-setup.md](docs/spec/vocalinux-setup.md) — Vocalinux voice dictation from the official AppImage release, system dependencies, app config, autostart and the Meta+S consuming shortcut
[docs/spec/imagemagick-setup.md](docs/spec/imagemagick-setup.md) — ImageMagick install from the Ubuntu archive plus the tuned security policy, idempotent and without a version chase  
[docs/spec/ffmpeg-setup.md](docs/spec/ffmpeg-setup.md) — ffmpeg install from the Ubuntu archive, idempotent and without a version chase  
[docs/spec/rustdesk-setup.md](docs/spec/rustdesk-setup.md) — RustDesk remote desktop client install from GitHub releases, public server registration, per-machine password and the network report ID  
[docs/spec/telegram-setup.md](docs/spec/telegram-setup.md) — Telegram Desktop install from the official redirect, launcher entry and the built-in auto-update
[docs/spec/playwright-setup.md](docs/spec/playwright-setup.md) — playwright-cli browser control install for the desktop user, over the chrome_setup CDP listener
[docs/spec/scrcpy-setup.md](docs/spec/scrcpy-setup.md) — scrcpy Android screen mirroring client from the GitHub release with the Ubuntu archive as the fallback, the version directory with the switched command link, the Android USB rules and the two menu entries  
Guides — how to work with the project:

[docs/guides/project-structure.md](docs/guides/project-structure.md) — repository layout, file responsibilities, config editing tools  
[docs/guides/project-rules.md](docs/guides/project-rules.md) — code conventions: output policy, datetime format, engineering standards  
[docs/guides/developer-guide.md](docs/guides/developer-guide.md) — quick start, running the test suite (uv run pytest), linting, type checking, CI, commit workflow, task best practices  
[docs/guides/planning-procedure.md](docs/guides/planning-procedure.md) — mandatory planning procedure for tasks that require a plan

Architecture decisions:

[docs/simplified-architecture.md](docs/simplified-architecture.md) — approved simplification rationale, resilience rule

Plans:

[docs/TODO.md](docs/TODO.md) — planned future work, ideas for new tasks
