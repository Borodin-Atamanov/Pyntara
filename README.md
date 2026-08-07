# Pyntara

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

Pyntara turns a fresh Kubuntu installation into a fully configured workstation or server in one command. It installs packages, creates users, derives passwords from a KeePass vault, configures ZRAM and swap, sets up SSH, deploys a local proxy with a remote tunnel, tunes the desktop environment, and enables encrypted telemetry reporting. All tasks are idempotent — safe to rerun. A single bootstrap script downloads the repo and launches the Python provisioning engine.

## Start

```bash
inst="$(mktemp /tmp/pyntara.XXXXXXXXX)" && curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o "$inst" https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash -c 'read -r -s -p "Enter production vault password: " p && PYNTARA_VAULT_PASSWORD="$p" bash "$1"' _ "$inst"
```

The installer runs non-interactively and never asks the user anything. The production vault password is optional: enter it once via read -s (hidden input) and pass it through the PYNTARA_VAULT_PASSWORD environment variable to use the production vault. Without a password, or with a password that matches no vault, the installer shows a short countdown notice and falls back to the default vault.

Optional environment variables can be added inside the sudo bash -c block, separated by spaces before the script invocation:

PYNTARA_VAULT_SOURCE — production or default. When omitted, the source is auto-detected from the password.

PYNTARA_INSTALL_MODE — minimal, server or desktop. When omitted, the mode is auto-detected from the system (desktop or server).

PYNTARA_TASKS — space-separated task names. When omitted, the default task set of the chosen mode is used.

PYNTARA_FORCE_TASKS — space-separated task names that must rerun even when the target state is already reached. When omitted, no task is forced. Invalid names are reported with a countdown notice and ignored.

PYNTARA_SKIP_APT_UPDATE — 1, true or yes skips the apt index refresh that add_extra_repos and cli_tools run before package operations. Use for test or offline runs; omit it in real provisioning so packages resolve from a fresh index.

Quick test run without the apt index refresh. The flag sits in the prefix of the script invocation, so it reaches the installer and the engine; a flag joined with && would only set a shell variable and never reach the installer:

```bash
inst="$(mktemp /tmp/pyntara.XXXXXXXXX)" && curl --fail --location -o "$inst" https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash -c 'read -r -s -p "Enter production vault password: " p && PYNTARA_VAULT_PASSWORD="$p" PYNTARA_SKIP_APT_UPDATE=1 bash "$1"' _ "$inst"
```

Engine values used by the Python part come from config.toml at the repository root: the task data root, the notice timeout, the command timeouts, the Ubuntu archive components enabled by add_extra_repos, the cli_tools package list with the install retry count and the success threshold, and the task catalog under [[tasks]] with each task's name, description, dependencies and mode membership. The file is mandatory; a missing or invalid file stops the run. The cli_tools task succeeds when at least cli_tools.package_success_threshold_percent of the configured packages are installed after the run; a single failing package is not fatal by itself.

The interactive installer variant does not work and its development is stopped.

## Documentation index

AI-Agent rules: `AGENTS.md`

Contracts — mandatory runtime specifications, must not be violated. Only MUST assertions testable in code:
`docs/contracts/architecture.md` — runtime boundaries, composition root, Context, resilience rule
`docs/contracts/bootstrap.md` — bootstrap installer contract for inst.sh
`docs/contracts/task-model.md` — task model, idempotency contract, catalog and dependencies

Spec — functional specification, what the system does and how. Design rationale, formulas, parameters. May reference contracts but never repeat them:
`docs/spec/install-modes.md` — minimal/server/desktop modes, auto-detection, task and force selection
`docs/spec/secrets-model.md` — KeePass vaults, passwords, PYNTARA_VAULT_PASSWORD, fallback
`docs/spec/telemetry.md` — encrypted PDF telemetry, queues, retries, Telegram and Google Drive
`docs/spec/networking.md` — local proxy server, proxy tunnel, NextDNS
`docs/spec/users-and-host.md` — users i/j/k, hostname, passwords, ZRAM, swap, NTP, power
`docs/spec/desktop-apps.md` — ImageMagick, FFmpeg, scrcpy, Kate, terminal, browsers

Guides — how to work with the project:
`docs/guides/project-structure.md` — repository layout, file responsibilities, config editing tools
`docs/guides/project-rules.md` — code conventions: output policy, datetime format, engineering standards
`docs/guides/developer-guide.md` — quick start, testing, CI, commit workflow

Architecture decisions:
`docs/simplified-architecture.md` — approved simplification rationale, resilience rule
