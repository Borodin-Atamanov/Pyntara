# Pyntara

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

Pyntara turns a fresh Kubuntu installation into a fully configured workstation or server in one command. It installs packages, creates users, derives passwords from a KeePass vault, configures ZRAM and swap, sets up SSH, deploys a local proxy with a remote tunnel, tunes the desktop environment, and enables encrypted telemetry reporting. All tasks are idempotent — safe to rerun. A single bootstrap script downloads the repo and launches the Python provisioning engine.

## Start

```bash
curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o inst.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash -c 'read -r -s -p "Enter production vault password: " p && PYNTARA_VAULT_PASSWORD="$p" bash inst.sh'
```

The installer runs non-interactively and never asks the user anything. The production vault password is optional: enter it once via read -s (hidden input) and pass it through the PYNTARA_VAULT_PASSWORD environment variable to use the production vault. Without a password, or with a password that matches no vault, the installer shows a short countdown notice and falls back to the default vault.

Optional environment variables can be added inside the sudo bash -c block, separated by spaces before bash inst.sh:

PYNTARA_VAULT_SOURCE — production or default. When omitted, the source is auto-detected from the password.

PYNTARA_INSTALL_MODE — minimal, server or desktop. When omitted, the mode is auto-detected from the system (desktop or server).

PYNTARA_TASKS — space-separated task names. When omitted, the default task set of the chosen mode is used.

The interactive installer variant does not work and its development is stopped.

## Documentation index

AI-Agent rules: `AGENTS.md`

Contracts — mandatory runtime specifications, must not be violated. Only MUST assertions testable in code:
`docs/contracts/architecture.md` — runtime layers, composition root, RunContext, dependency injection
`docs/contracts/bootstrap.md` — bootstrap installer contract for inst.sh
`docs/contracts/interactive-ui.md` — dialog-based interactive terminal UX flow
`docs/contracts/task-model.md` — task protocol, TaskResult, idempotency contract, full task catalog with dependencies

Spec — functional specification, what the system does and how. Design rationale, formulas, parameters. May reference contracts but never repeat them:
`docs/spec/install-modes.md` — minimal/server/desktop modes, auto-detection, timers
`docs/spec/secrets-model.md` — KeePass vaults, passwords, PYNTARA_VAULT_PASSWORD, fallback
`docs/spec/telemetry.md` — encrypted PDF telemetry, queues, retries, Telegram and Google Drive
`docs/spec/networking.md` — local proxy server, proxy tunnel, NextDNS
`docs/spec/users-and-host.md` — users i/j/k, hostname, passwords, ZRAM, swap, NTP, power
`docs/spec/desktop-apps.md` — ImageMagick, FFmpeg, scrcpy, Kate, terminal, browsers

Guides — how to work with the project:
`docs/guides/project-structure.md` — repository layout, file responsibilities, config editing tools
`docs/guides/project-rules.md` — code conventions: output policy, datetime format, engineering standards
`docs/guides/developer-guide.md` — quick start, testing, CI, commit workflow
