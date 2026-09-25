# btrfs storage, recompression and recovery points

Three tasks work on the filesystem of a machine that was installed on btrfs: btrfs_setup readies the filesystem, btrfs_recompress rewrites the data that is already on it and balances the chunks, and btrfs_points_setup stores the immutable save point with its writable work copy. A machine whose root is not btrfs is left alone by all three: such a machine stays a working machine, and each task reports one warning of a completed task.

## Mission

A machine is installed, the user works on it, and at some point the system stops booting or stops behaving. The user then chooses a point in the boot menu, works from it, and continues with a working system that writes to the disk as any ordinary system does. The point itself is never written to, so it can be used for the next recovery as well. This is what the three sections together provide: compressed storage that gains room over time, and a saved state the user can return to without a developer, a rescue medium or a reinstall.

## Compression

The storage setup writes compress=zstd:15 into the option field of the fstab lines of the root and of the home of the running system, and applies the options again with a remount, so the compression is in force without a reboot. zstd level 15 is the highest level the kernel accepts for this parameter; the level is chosen once and pays for itself in every write that follows, because the machine writes far more often than it rewrites its whole data set.

The rewrite of the data that predates the option is the job of the recompression section. That job runs in the background as a transient systemd unit, so the rest of the provisioning continues while it runs, and its journal appears in a console window on the desktop of the user, who is not a developer and needs to see that the machine is working rather than waiting. The job rewrites every declared mount point with btrfs filesystem defragment -r -c<algorithm> -L <level> -f and then runs btrfs balance start -dusage=73 --full-balance, which collects the space the rewrite freed. A rewrite of a mount point does not descend into a subvolume or a mount point of its own, so the swap area under /swap never reaches that step and cannot make it fail. Free space is checked before the rewrite and inside the program, because a rewrite allocates new extents before it releases the old ones. The rewrite of a whole filesystem takes minutes and the btrfs tool prints nothing while it works, so the program says which mount point it is working on and prints the result with the time the step took as soon as that step ends; the window therefore shows a working machine and, a few minutes later, the sizes and the free space it gained.

The job writes a marker file when every step succeeded. The marker is what makes the work one-off: a later run finds it and skips the rewrite, and the forced run of the task removes the marker first, so a run that asks for the work again really performs it. A failing step leaves no marker and exits nonzero, so a later run tries again.

The rewrite belongs before the machine carries its first save point, and the catalog puts the recompression section before the points section for exactly that reason. The reason is space, not taste: a point shares its extents with the root it was taken from, so rewriting the root of a machine that already carries points copies all the shared data into new extents instead of replacing them, and the machine needs room for that copy. A machine that is left with its one-off work unfinished because a run was interrupted still carries the marker of nothing and repeats the work on the next run, which is why the free space is checked before the rewrite starts.

## The points subvolume

The save points live in a top-level subvolume named @points, mounted at /points. The top level matters: a point stored inside the root subvolume would travel away with the root the moment the root subvolume is renamed, which is exactly what a recovery does. The storage setup creates the subvolume when it is missing, writes its fstab line from the device field of the root line (so no identifier is written into the code, and a machine that names its device by UUID, by label or by path is served the same way) and mounts it.

## The swap area

The swap area of a machine installed on btrfs lives in a top-level subvolume named @swap, mounted where the swap file of the swap section lies (/swap). The subvolume is what keeps the two btrfs refusals away from the machine: the kernel refuses to snapshot a subvolume that carries an active swap file of this filesystem, and it refuses to activate a swap file whose extents a snapshot shares. A swap file inside the root subvolume therefore either blocks the save point or is left dead by it, and a machine whose point was taken that way never gets its disk swap back while the point exists. The storage setup creates this subvolume the same way as the points subvolume, writes its fstab line from the same root line and mounts it; the swap section then creates its file inside the mounted subvolume. The path of the swap area is declared once, in the values of the swap section, and the storage section reads it from there.

A machine configured before the swap area had a subvolume of its own carries its swap file inside the root subvolume. The storage section moves the area: it stops the swap service, removes that file, creates and mounts the subvolume, and starts the swap service again, which creates a fresh file inside the subvolume. The old file cannot be kept, because a file whose extents a snapshot shares cannot be activated any more, and its removal returns to the machine the room it held; the path and the size of the removed file are named in the log. A machine whose swap area is a mounted filesystem of its own, for example a swap partition, is left untouched: such a machine keeps no swap file of this filesystem inside its root subvolume.

## The save point and the work copy

The point is created with btrfs subvolume snapshot -r / <points>/Pyntara-permanent: a read-only snapshot of the running root. A read-only subvolume of btrfs is a point in time that no session can change, and its property is verified after the snapshot with btrfs property get <path> ro. An existing point is never recreated and never written to, because it is the state the user returns to; a point that answers that it is writable is reported as a warning instead. The snapshot needs no release of the swap of the machine: the swap area lies in a subvolume of its own, and a subvolume is a barrier for a snapshot, so the point never carries the swap file and the swap stays active while the point is taken. Measured on the target machine on 2026-09-25: a swap file inside the root subvolume makes the kernel refuse the snapshot while that swap is active ("cannot snapshot subvolume with active swapfile", which btrfs reports as "Could not create subvolume: Text file busy") and refuse to activate that same swap file again once a snapshot shares its extents ("swapon failed: Invalid argument", with "swapfile must not be copy-on-write" in the journal); with the swap area in a subvolume of its own both refusals disappear.

The work copy is a writable snapshot of the point, btrfs subvolume snapshot <point> <points>/Pyntara-work. It is an ordinary system: it boots its own subvolume directly, writes to the disk, and carries the daily work. An existing work copy is never overwritten, because it carries the work of the user; a machine whose copy broke is repaired by storing a new one from the point.

A snapshot of the root contains the subvolumes of that root, and a subvolume nested inside it is not part of the snapshot. The home of the user is a subvolume of its own on this design, so the point holds the system and the installed programs, while the user data lives in the real home of the machine and is the same home in every session, whether the user came from the point, from the work copy or from the ordinary system. The swap area is a subvolume of its own on a machine these sections built, mounted where the swap file lies, so the point holds neither the swap file nor the swap area.

## The boot menu

The generator grub-btrfs is not in the Ubuntu archive, so the storage setup builds it from pinned sources (commit 38cd2fa419e4c1c0f1e345a374b37c040c170047, make install into the grub.d directory) when its installed file is absent, and starts its daemon with the drop-in that watches the points mount. The generator writes one entry per kernel for every subvolume it lists, and it applies one kernel parameter line to all of those entries. That single line is set empty, so every generated entry boots its subvolume directly, which is what makes the work copy a normal system that writes to the disk.

The immutable point needs the opposite: its session must keep the root filesystem in memory. The points section therefore writes its own entry into /etc/grub.d/40_pyntara_permanent_entry with overlayroot=tmpfs:recurse=0, the search line built from the device field of the root fstab line, and one menu entry per kernel found inside the point. The newest kernel carries the plain name of the point and every older kernel carries its version in the title. A kernel without its initial ramdisk gets no entry, because such an entry cannot boot, and the section says so in a warning. The point is added to GRUB_BTRFS_IGNORE_SPECIFIC_PATH, so the generated list leaves it out and the menu shows the point exactly once, as the hand written entry with the in-memory root.

The menu is rebuilt with update-grub while the daemon of the generator is stopped: a rebuild that races the daemon can leave the generated file missing, and the menu then loses the whole snapshot submenu. The rebuild happens when the section changed something, and always in the forced run, which is how a kernel that changed inside a work copy reaches the menu, because the daemon watches its directories in a way that does not see a file change inside a subvolume.

## Maintenance

The storage setup writes the maintenance lines of the btrfsmaintenance package and sets the state of its timers: the scrub runs weekly and the balance runs weekly, both at an idle CPU priority, on the root filesystem. Defragmentation and the periodic trim stay off: defragmentation is the one-off job of the recompression section, and a trim belongs to the settings of the device itself. The recompression section waits for its job to end before taking the point, so the point carries the finished state of the machine; that wait is bounded by a declared limit, and a machine whose job runs longer still receives its point from the state it has by then.

## Recoverable failures

Every step of the three sections reads the state of the machine before it writes and does nothing when the state is already the intended one, so a rerun on a configured machine is cheap and never touches the user data. A step that cannot be performed becomes a warning of a completed task: the remaining steps and the remaining tasks still run, and the entry point exits nonzero, so an incomplete configuration is visible to scripts. The tasks never delete a point, a work copy or a file of another package.

A warning carries the cause and not only the fact: a command that failed is reported with the text it answered, because a machine where a mount, a snapshot or a menu rebuild fails is read by its user and not by a developer, and a message that says only that something failed leaves that user with nothing to do. Two cases are told apart where a path a section owns is already taken: the path carries the subvolume the section would store, and the section leaves it alone as the state the user keeps; the path carries a directory, and the section refuses it, because a snapshot into an existing directory is stored inside that directory under another name, where nobody looks for it, so the section names the path and asks for it to be moved aside.

## Parameters

All parameters live in the values modules of the sections: src/pyntara/values/btrfs_setup.py, src/pyntara/values/btrfs_recompress.py and src/pyntara/values/btrfs_points_setup.py.

packages - the packages of the storage section: btrfs-progs, btrfsmaintenance, btrfs-compsize, augeas-tools, overlayroot, inotify-tools and make

compression_option_assignment - the option the fstab lines receive, compress=zstd:15
compressed_mount_points - the mount points whose lines receive it, the root and the home
points_subvolume_name - the top-level subvolume that holds the points, @points
points_mount_point - the mount point of that subvolume, /points
swap_subvolume_name - the top-level subvolume that holds the swap area, @swap; its mount point is the directory of the swap file, read from the values of the swap section
toplevel_subvolume_id - the id of the top level of a btrfs filesystem, 5
grub_btrfs_commit - the pinned commit of the menu generator
maintenance_directives - every line the section owns in the configuration of btrfsmaintenance
maintenance_enabled_timers, maintenance_disabled_timers - the timers the section turns on and off

compression_algorithm, compression_level - the compression of the one-off rewrite, zstd at level 15
defragmented_mount_points - the mount points the rewrite walks, the root and the home
balance_usage_percent - the chunk fill percentage the balance rewrites, 73
minimum_free_gib - the room the machine must have before the rewrite starts
defragment_timeout_seconds, balance_timeout_seconds - the bounds of the two steps
program_deploy_path - the path the program of the section is deployed to
done_marker_path - the file whose presence means the one-off work is done
job_unit_name - the unit the job runs as, pyntara-btrfs-recompress
window_unit_name - the unit of the console window that shows the journal of the job
job_wait_poll_seconds, job_wait_limit_seconds - how often and how long the points section waits for the job

point_name, work_copy_name - the names of the save point and of its writable copy, Pyntara-permanent and Pyntara-work
grub_d_entry_path, grub_d_entry_file_mode - the file of the boot entry of the point and its mode
overlay_parameter - the kernel parameter that keeps the root filesystem in memory, overlayroot=tmpfs:recurse=0
grub_btrfs_ignore_key - the setting that keeps the point out of the generated list
snapshot_timeout_seconds, update_grub_timeout_seconds - the bounds of the snapshot and of the menu rebuild

## Verified behaviour

Every mechanism above was verified on a live Kubuntu 26.04 machine before the code was written: a point stored in @points is listed by the generator and boots with the root filesystem in memory while the point itself stays read-only; the work copy boots its own subvolume with no overlay and keeps its writes across a reboot; the generator leaves the point out of its list once the setting names it; renaming the running root subvolume works from a normal session; the fstab line of the points subvolume is accepted by findmnt --verify; a rebuild with the daemon stopped always leaves the generated file in place; and the console window of the job appears on the desktop of the user through the service manager of that user.

One limitation is known and stated rather than hidden: a kernel that changes inside a work copy does not reach the boot menu through the daemon, so the forced run of the points section is the way to refresh the menu after such a change.
