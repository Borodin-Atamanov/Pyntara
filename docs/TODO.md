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
rest were that overwrite.

The plan for the remaining work is docs/kde-settings-plan.md. The per-layout
switching key is finished (2026-09-26): the hotkey client works in two phases, it
takes every claimed combination from the action that holds it, registers the
target action (the call the KGlobalAccel client library makes) and gives it the
combinations, and the keyboard task does that before its compositor restart,
because kwin reads the combination of a layout action when it starts. Measured
with the same code on a configured machine and on a fresh one: the run reports no
shortcut warning, pressing Meta+E switches the layout to Spanish, and the setting
survives a reboot. What remains open is the overwrite by the other owners
(plasmashell over appletsrc, powerdevil over powerdevilrc) and the reload of the
appearance into the running session.

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

## Telegram Desktop is skipped when a single 15 s probe gets no answer

Clean-machine run of 2026-09-25 17:56 with the default vault: the section resolved
the release and then skipped the download and the installation, warning "cannot
resolve https://telegram.org/dl/desktop/linux: the host did not answer within 15 s
(curl exit 28), so the download is skipped instead of retrying for up to 7777 s", so
the machine stayed without the program the mode asks for. The message is built in
src/pyntara/tasks/telegram_setup.py. The behaviour is deliberate and written down in
docs/spec/telegram-setup.md: a host that does not answer within
reachability_probe_timeout_seconds (15, src/pyntara/values/telegram_setup.py) counts
as a blocked destination, and its reason is reported at once instead of spending the
retry budget of the resolve (curl_retry_max_time_seconds 7777,
src/pyntara/values/engine.py), which is what a network that drops the packets would
turn into one connect timeout per attempt. So this is not a defect but a choice of
behaviour, and the choice is the user's: keep the short probe, raise its budget, or
probe a silent host again before it is called blocked. The cost of the present choice
is visible in the run above, where one silence of 15 s left the machine without the
program.

## Enable the atd service for the at and batch commands

The at package is part of cli_tools_lite_setup now, but atd must also be enabled
and started, because batch does nothing without it. The console section is a
package-only section today: it carries no service step at all, so the step
either belongs to a section that already owns services or the package section
receives a service step with its own tests and its own specification line.
