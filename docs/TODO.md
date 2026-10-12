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

## Хром, исправить в репозитарии настроек хрома, чтобы вначале там окна лишние - принять правила хрома - лишнее и второе, с галочками, предлагающими сделать его браузером по-умолчанию, надо чтобы они не поялвлись эти окна.

## A healthy desktop run ends with exit code 1

Fresh run on clean010 of 2026-09-29, mode desktop auto-detected, default vault:
36 of 36 tasks done, and the run exited 1, because kde_settings warns that the
session runs the power profile balanced while the configuration asks for
performance. The profile belongs to power-profiles-daemon and the task does not
switch it, so the warning is present on any machine whose live profile is not
already performance, and the task contract turns every warning into a nonzero
exit code of the run. The machine is fully configured, yet the run reports
failure to a script that watches the exit code, and the bootstrap prints
"Pyntara installer finished with exit code 1" to the user.
Not a defect of the task, which reports both names instead of claiming the
configuration was applied: the open question is the severity. Options: the task
switches the profile through power-profiles-daemon when it differs, or it
reports the difference as a non-failing notice, or the run keeps exiting nonzero
and the behaviour is documented as intended.

## The local proxy message names the wrong object

Clean010 run of 2026-09-29: three_x_ui_xray_setup logged "no vless link in
xray_client_profile of default.vault: the local proxy is not configured" while
the local proxy inbound on 127.0.0.1:10800 was created in the same stage and the
task result says "local proxy on 127.0.0.1:10800 configured". What is missing is
the remote link the machine would leave through, not the local proxy. The
message is built in src/pyntara/xray_local_proxy.py (_remote_profile), and the
same wording is used for a missing source vault and for an unusable link. The
line misleads whoever reads the run log, so the sentence should name the missing
remote path instead.

## Third-party installers write credentials and noise into the run log

Clean010 run of 2026-09-29, evidence from the log: the 3x-ui installer prints the
panel login, the password, the port, the web base path and the API token in plain
text, together with its own demand to keep them safe, so the run log carried the
credentials of the panel and was protected by nothing but its file mode until the
mode was fixed to 0600; the rustdesk deb prints a red "Failed to stop
rustdesk.service: Unit rustdesk.service not loaded" on a fresh machine; apt prints
"debconf: unable to initialize frontend" and pip prints the "Running pip as the
'root' user" warning on package steps. None of these is produced by Pyntara, and
the panel credentials are the only serious one: consider whether the task keeps
the credential block of the installer out of the run log, or the file mode stays
the protection.

## Duplication that still needs a deduplication plan

The branch unify-unit-placeholder (2026-10-12) then made the unit name uniform: every
placeholder spelling ({unit_name}, {service_unit_name}) became {unit} across src,
task_data and the specs, and the systemctl command vocabulary now lives once in
values/engine.py, so the seven sections that drive a unit of their own
(btrfs_setup, port_forwarding_setup, swapfile_service_install, system_metrics_setup,
upnp_forwarding_setup, zram_service, zswap_service) and their tasks read those values
instead of keeping their own copies. What remains is listed here; each
item needs its own plan before any code is touched, because a copy silently changed
in one place is a defect on the target machine.

1. The wider service-task orchestration is copied across swapfile_service_install,
zram_service and zswap_service: install the packages, deploy the program, render the
unit, run the program and wait. The unit write, the daemon-reload and the enable
step are shared now (systemd_unit.install_and_enable_unit); what is not shared is
the rest of the flow, and zswap_service keeps its own sequence because it writes the
unit plainly and enables the service without the autorun check.

2. The browser task body is copied across chrome_setup and firefox_setup: the values
guard, the settings repository sync, the deploy of the system tree, the profile
apply and the launcher pin.

3. The release-service orchestration is copied across i2pd_service_setup, tor_setup,
yggdrasil_service_setup and rustdesk_setup: the service enable, the status check and
the start with the wait for active. The saved address match is shared now
(utils.saved_address_file_matches).

4. Values modules repeat comment and constant blocks: swapfile_service_install
against zram_service, ssh_client_setup against ssh_daemon_setup, ssh_daemon_setup
against tor_setup, dnsproxy_setup against rustdesk_setup, scrcpy_setup against
vocalinux_setup.

5. The btrfs sections (btrfs_setup, btrfs_points_setup, btrfs_recompress) share
short blocks that one shared btrfs helper could take.

6. The test suite repeats fixtures and fake runs: most in
test_swapfile_service_install against test_zram_service, then test_kde_keyboard_setup
against test_kde_settings, test_i2pd_service_setup against
test_yggdrasil_service_setup, test_cli_tools_heavy_setup against
test_cli_tools_lite_setup and test_configure_swapfile against test_configure_zram.

A plan for any item states the mechanism that makes the copy drift, the shared
module or value that removes it, the call sites and tests it touches, and the live
run that proves the behaviour is unchanged on a target machine.

