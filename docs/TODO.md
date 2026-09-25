# TODO

Planned future work. После реализации - удаляем из этого файла.

## Bring the desktop settings to the running session

The desktop tasks write config files, and a running KDE component that owns such
a file writes its own memory back over it later. Measured on 2026-09-19: one
kwriteconfig6 --notify on kxkbrc made every running component save its state
(plasma-org.kde.plasma.desktop-appletsrc 19:15:27, katerc 19:15:58,
kglobalshortcutsrc 19:15:59, kwinrc 19:16:12), which dropped values the run had
written. A read-only audit of 335 configured desktop values on a provisioned
machine found ten that did not match: six were wrong paths inside the values
themselves (the home of the recording machine; fixed with the home_dir
placeholder), one was a tab box layout name this KDE no longer ships, and the
rest were that overwrite. The plan: after the desktop tasks write their config,
make the owners read it, that is restart the compositor through
org.kde.KWin.replace, restart plasmashell for appletsrc and powerdevil for
powerdevilrc, start the session services that stay inactive, and give the
KGlobalAccel combinations again, because the actions that switch a keyboard
layout exist only once kwin holds the layouts; then decide how a written file
survives its owner, and wait by polling the real state instead of sleeping,
because the restart takes minutes on a weak machine. The recipe measured so far
is in docs/spec/kde-keyboard-setup.md. Needs a live KDE stand: the machine used
for these measurements is gone.

## Install antivirus antirootkit.

Find the best solutions, choose the best, create task to install it.

## Настроить btrfs системы, чтобы появились нужные параметры при загрузке системы

## Install at through cli_tools_lite_setup

Add the at package to PACKAGES of cli_tools_lite_setup
(src/pyntara/values/cli_tools_lite_setup.py), so every mode receives the at and
batch commands with the rest of the console set, and make the task ensure the
atd service is enabled and running, because batch does nothing without it. The
reason is the btrfs maintenance of a desktop: batch runs a queued command when
the average system load is low, which is the idle trigger a background
recompression or deduplication needs, while a systemd timer has no idle
condition and cron misses its window on a machine that is often off. Requested
by the user on 2026-09-25.

