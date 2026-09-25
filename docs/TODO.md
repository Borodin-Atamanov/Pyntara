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
## A machine can run without disk swap while the swap section reports itself configured

Machine: Kubuntu 26.04 test machine clean001, kernel 7.0.0-30-generic, btrfs-progs
6.17.1, root btrfs /dev/vda2[@]. State before the run: /swap was a plain directory
inside the root subvolume with a 13.2 GiB swap file, the save point
Pyntara-permanent was stored, and the swap file had been part of that point's
snapshot because the storage step released the swap before taking the snapshot.

Run of 2026-09-25 19:31 with the branch code (swapfile_service_install btrfs_setup
btrfs_points_setup):
- the swap program answered {"changed": false, "skipped_reason": null, "error":
  "activating the swap file failed: swapon: /swap/swapfile: swapon failed: Invalid
  argument"};
- the task warned "the swap program failed: activating the swap file failed: ..."
  and "the boot service could not be started: Command '['systemctl', 'start',
  'swapfile.service']' returned non-zero exit status 1";
- the summary line of the same task said "already configured";
- swapfile.service stayed failed and the machine kept no active swap except the
  eight zram devices;
- the kernel named the cause: "BTRFS warning (device vda2): swapfile must not be
  copy-on-write", because the file's extents are shared with the point's snapshot;
- the points section finished in 0.039 s and did not mention the swap at all;
- the run exited 1 with two warnings.

Measured the same day in an isolated btrfs image inside a loop file: once a
subvolume that holds a swap file has been snapshotted, swapon refuses that same
file; a freshly created swap file in the same subvolume activates; deleting the
snapshot makes the old file activatable again. So a machine can be left without
disk swap for good, and the only text its user sees is a util-linux message that
names neither the cause nor a way out.

## The console window of the recompression cannot be opened on a second run

Run of 2026-09-25 19:31:52: "systemd-run --machine i@.host --user
--unit=pyntara-btrfs-recompress-window --collect /usr/bin/konsole -e journalctl -f
-u pyntara-btrfs-recompress" exited 1 with "Failed to start transient service unit:
Unit pyntara-btrfs-recompress-window.service was already loaded or has a fragment
file." The section reported it as the warning "the window that shows the
recompression could not be opened: ...". The job itself kept running, so the user
loses the visible progress window while the work goes on unseen.

## The recompression job fails on a swap file inside the root subvolume and leaves no marker

Journal of pyntara-btrfs-recompress, run of 2026-09-25 18:55:52: "run: btrfs
filesystem defragment -r -czstd -L 15 -f /" ended after 756.660 s with "ERROR:
defrag failed on /swap/swapfile: Text file busy" and exit status 1, the swap file
being active at that moment. The program then rewrote /home (7.495 s), balanced the
chunks ("Done, had to relocate 3 out of 26 chunks"), reported "the one-off work did
not finish, so no marker was left: rewrite of / failed with status 1" and exited 1.
/var/lib/pyntara/btrfs-recompress-done is absent, so the next run repeats the whole
rewrite and the balance: measured 12 min 52 s of wall clock and 1 GiB peak memory
for that attempt, on a filesystem that carried the swap file inside the root
subvolume. The points section waits for that job up to its declared limit of
10800 s.

## Telegram Desktop is skipped without a retry when a single 15 s probe fails

Clean-machine run of 2026-09-25 17:56 with the default vault: the section resolved
the release and then skipped the download, warning "cannot resolve
https://telegram.org/dl/desktop/linux: the host did not answer within 15 s (curl
exit 28), so the download is skipped instead of retrying for up to 7777 s". The
message is built in src/pyntara/tasks/telegram_setup.py (lines 167-170), the probe
budget is REACHABILITY_PROBE_TIMEOUT_SECONDS = 15 in
src/pyntara/values/telegram_setup.py, the retry budget is
CURL_RETRY_MAX_TIME_SECONDS = 7777 in src/pyntara/values/engine.py. The machine
stayed without the program the mode asks for.

## The countdown notice of the default vault promised by the README does not appear for the shipped password

README.md lines 36-38 promise: "While the line keeps the shipped value, and also
when a password opens no vault, the installer shows a short countdown notice and
falls back to the default vault". inst.sh treats the shipped password as an
auto-detected source and prints no notice ("Vault password from environment,
auto-detected source: default", inst.sh lines 434-441); the countdown appears only
for a password that matches no vault (inst.sh line 443) and for a missing password
(inst.sh line 452). The clean-machine run of 2026-09-25 kept the shipped password
and its log carried no countdown.

## The keep-debs note of add_extra_repos reads as a sentence fragment

src/pyntara/tasks/add_extra_repos.py line 224 returns the note "keep downloaded
.deb files after install disabled" for the run log; the enabled form next to it is
"keep downloaded .deb files after install enabled". Both read as fragments rather
than as a sentence about the state that was applied.

## The shortcut warning of kde_settings names no component and no reason

src/pyntara/tasks/kde_settings.py lines 1241-1249 build the warning "the daemon
does not hold the configured shortcuts: [(action, after, requested), ...], they are
written into the shortcut file for the next login". The tuples carry the action
code and the two key lists, while the component and the friendly name are dropped
by the zip unpacking, and no reason for the refusal is reported.
