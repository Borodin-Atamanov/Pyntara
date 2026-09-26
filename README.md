# Pyntara 0.3.783

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

Pyntara turns a fresh Kubuntu installation into a fully configured workstation or server
in one command. It installs packages, configures ZRAM and swap, sets up SSH, DNS and
anonymity services (dnsproxy, i2pd, yggdrasil, Tor), tunes the desktop environment. All tasks are idempotent — safe to rerun. A
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
to the default vault; the installer writes a WARNING line for that fallback into the run log
and the run reports it as a warning of its own, so a run that took the secrets of the machine
from the repository test vault never passes silently.

PYNTARA_INSTALL_MODE — one of the mode names declared in the task catalog
(src/pyntara/values/tasks.py). The names in use are minimal, server, desktop and
fast_desktop, where fast_desktop is the quick set: a working system reachable from
outside, without the long heavy installs. A written name is read without case and
with a hyphen and an underscore counting as one separator, so fast-desktop selects
fast_desktop; a name that declares no mode shows a notice that names the declared
modes, the run applies the auto-detected mode and reports that substitution as a
warning of the run. The commented mode lines are written by
python -m pyntara.launcher_modes, so uncommenting one is the whole choice. Commented
out; when omitted, the engine detects the mode from the system (desktop or server).

PYNTARA_TASKS — space-separated task names, the whole catalog sits in a commented line written
by python -m pyntara.launcher_modes.
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

PYNTARA_DELETE_PACKAGES_AFTER_INSTALL — 1, true or yes, and an absent variable, delete
what a task downloaded once it has served its purpose; 0 keeps it instead, so a repeated
run reuses it and saves network traffic and time. The run then releases the downloads as
it goes and stops before a task when the machine has no room left, which it reports as a
warning of the run ([docs/contracts/architecture.md](docs/contracts/architecture.md),
Resilience rule; the accepted answers of the variable are in
[docs/contracts/bootstrap.md](docs/contracts/bootstrap.md)).

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
[docs/spec/keyring-setup.md](docs/spec/keyring-setup.md) — the KDE wallet of a machine that logs in automatically is created without a password, so no keyring password dialog appears  
[docs/spec/btrfs-setup.md](docs/spec/btrfs-setup.md) — btrfs compression, the one-off recompression that runs as a watchable background job, the maintenance timers, and the immutable save point with its writable work copy in the boot menu
Guides — how to work with the project:

[docs/guides/project-structure.md](docs/guides/project-structure.md) — repository layout, file responsibilities, config editing tools  
[docs/guides/project-rules.md](docs/guides/project-rules.md) — code conventions: output policy, datetime format, engineering standards  
[docs/guides/developer-guide.md](docs/guides/developer-guide.md) — quick start, running the test suite (uv run pytest), linting, type checking, CI, commit workflow, task best practices  
[docs/guides/planning-procedure.md](docs/guides/planning-procedure.md) — mandatory planning procedure for tasks that require a plan

Architecture decisions:

[docs/simplified-architecture.md](docs/simplified-architecture.md) — approved simplification rationale, resilience rule

Plans:

[docs/TODO.md](docs/TODO.md) — planned future work, ideas for new tasks
