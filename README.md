# Pyntara 0.3.718

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

Pyntara turns a fresh Kubuntu installation into a fully configured workstation or server
in one command. It installs packages, configures ZRAM and swap, sets up SSH, DNS and
anonymity services (dnsproxy, i2pd, yggdrasil, Tor), tunes the desktop environment, and
enables encrypted System Metrics reporting. All tasks are idempotent — safe to rerun. A
launcher script downloads the bootstrap installer, and the installer fetches the repository
and launches the Python provisioning engine.

## Start

The launcher pyntara.sh downloads the bootstrap installer from the raw branch of the
repository and runs it as root. Every parameter of the run lives inside the file, so the
command line stays short and nothing has to be exported. Download it into the shared
memory, edit the parameters, run it:

```bash
curl --fail --location --connect-timeout 60 --retry 17 --retry-delay 3 --retry-all-errors --retry-max-time 7777 --retry-connrefused \
-o /dev/shm/pyntara.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/pyntara.sh
$EDITOR /dev/shm/pyntara.sh
sudo bash /dev/shm/pyntara.sh
```

/dev/shm is a memory filesystem, so the launcher never reaches the disk and disappears at
the next reboot, and a password written into the file is not left on the machine. The
launcher ignores the environment of the caller: the parameters are the values of the file,
and a variable left commented out is resolved by the installer or the engine itself.

The vault password is the PYNTARA_VAULT_PASSWORD line. The value shipped there is the
published password of the default vault, and a user who knows the production vault password
replaces it before the run; the production password is never committed and never written to
a log. The installer runs non-interactively and never asks the user anything: the vault
source is auto-detected from the password, production when it opens production.vault and
default when it matches default.password. While the line keeps the shipped value, and also
when a password opens no vault, the installer shows a short countdown notice and falls back
to the default vault.

Parameters of the file:

PYNTARA_REPO_URL, PYNTARA_REPO_BRANCH — repository and branch of the run. The branch selects
both the downloaded installer and the checkout the installer clones, so a branch run differs
from a main run by one value.

PYNTARA_INSTALL_MODE — minimal, server or desktop. Commented out; when omitted, the mode is
auto-detected from the system (desktop or server).

PYNTARA_TASKS — space-separated task names, the whole catalog sits in a commented line.
When omitted, the default task set of the chosen mode is used; dependencies are resolved
inside the engine, so a listed task always runs with what it needs.

PYNTARA_FORCE_TASKS — space-separated task names that must rerun even when the target state
is already reached. When omitted, no task is forced. The keyword that forces every task of
the resolved run set is `force_all_keyword` of the engine values module (`all` by
default). Task names and that keyword are compared without case. Invalid names are
reported with a countdown notice and ignored.

PYNTARA_SKIP_APT_UPDATE — 1, true or yes skips the apt index refresh that inst.sh,
add_extra_repos and the package install tasks run before package operations. The answers that mean true are
`environment_flag_true_values` of the engine values module (1, true and yes by default), compared
without case. Use it for test or offline runs;
omit it in real provisioning so packages resolve from a fresh index.

PYNTARA_LOG_DIR, PYNTARA_LOG_FILE, PYNTARA_JOURNAL_IDENTIFIER — one log for the whole run.
The launcher and the installer append to the same file under /var/log/pyntara, and both
report to the system journal under the identifier pyntara-install, which the engine mirrors
under its own identifier. The launcher logs the download phase, which no later layer sees.

Optional: PYNTARA_VAULT_SOURCE — production or default. The launcher leaves it unset, so the
source is auto-detected from the password as described above.

Values live in Python modules under src/pyntara/values/, one module per task, and
a task reads the values of its own module, so a value is never written in two
places. There is no second source: the config/ directory and its TOML documents are
gone, so a machine runs the values the deployed package carries
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
[docs/spec/chrome-setup.md](docs/spec/chrome-setup.md) — Google Chrome from the official apt repository, the settings repository applied to the live profile, the desktop entry with the local proxy and the CDP listener, and the boot unit that keeps the profile mirror mounted
Guides — how to work with the project:

[docs/guides/project-structure.md](docs/guides/project-structure.md) — repository layout, file responsibilities, config editing tools  
[docs/guides/project-rules.md](docs/guides/project-rules.md) — code conventions: output policy, datetime format, engineering standards  
[docs/guides/developer-guide.md](docs/guides/developer-guide.md) — quick start, running the test suite (uv run pytest), linting, type checking, CI, commit workflow, task best practices  
[docs/guides/planning-procedure.md](docs/guides/planning-procedure.md) — mandatory planning procedure for tasks that require a plan

Architecture decisions:

[docs/simplified-architecture.md](docs/simplified-architecture.md) — approved simplification rationale, resilience rule

Plans:

[docs/TODO.md](docs/TODO.md) — planned future work, ideas for new tasks
