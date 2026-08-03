# Pyntara

Pyntara is an automated Kubuntu provisioning system.
Primary target platform: Kubuntu 26.04 and newer with KDE, Wayland.

## Start

```bash
curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o insta.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash inst.sh
```

## Documentation index

AI-Agent rules: `AGENTS.md`

Contracts — mandatory runtime specifications, must not be violated:
`docs/contracts/architecture.md` — runtime layers, composition root, RunContext, dependency injection
`docs/contracts/bootstrap.md` — bootstrap installer contract for inst.sh
`docs/contracts/interactive-ui.md` — dialog-based interactive terminal UX flow
`docs/contracts/task-model.md` — task protocol, TaskResult, idempotency contract

Spec — functional specification, what the system does:
`docs/spec/bootstrap-flow.md` — startup flow: package install, git clone, uv sync, Pyntara launch
`docs/spec/install-modes.md` — minimal/server/desktop modes, auto-detection, timers
`docs/spec/secrets-model.md` — KeePass vaults, passwords, PYNTARA_VAULT_PASSWORD, fallback
`docs/spec/tasks-catalog.md` — all tasks with descriptions, ordering, and dependencies
`docs/spec/telemetry.md` — encrypted PDF telemetry, queues, retries, Telegram and Google Drive
`docs/spec/networking.md` — local proxy server, proxy tunnel, NextDNS
`docs/spec/users-and-host.md` — users i/j/k, hostname, passwords, ZRAM, swap, NTP, power
`docs/spec/desktop-apps.md` — ImageMagick, FFmpeg, scrcpy, Kate, terminal, browsers

Guides — how to work with the project:
`docs/guides/project-structure.md` — repository layout, file responsibilities, config editing tools
`docs/guides/project-rules.md` — code conventions: output policy, datetime format, engineering standards
`docs/guides/developer-guide.md` — quick start, testing, CI, commit workflow
