# Users, host, and system settings

This document specifies system-level parameters for tasks defined in `docs/contracts/task-model.md`.
Task descriptions and dependencies are in the catalog; this document covers only configuration details.

## Hostname

Task: hostname. The machine hostname is a random proquint word pair: hostname.hostname_random_bytes random bytes encoded by the shared proquint_encode helper into five-letter words joined by a dash, for example lusab-babad from the shipped count of four bytes. The randomness comes from the secrets module, so the name is cryptographically strong: the hostname feeds password generation (docs/spec/secrets-model.md) and the deterministic NextDNS profile choice (docs/spec/nextdns-profile.md), so it must not be guessable.

The task writes the name into the configured hostname.hostname_file and applies it to the running kernel through the configured hostname.set_hostname_command, so socket.gethostname() returns the new name for the dependent tasks. The task is idempotent: it is done when the hostname file already carries a name that decodes as a proquint (so it was set by this task) and the kernel already knows it; force mode always generates a fresh name.

All parameter values live in the src/pyntara/values/hostname.py: hostname_file, hostname_random_bytes and set_hostname_command.

## ZRAM

ZRAM is configured based on CPU core count.
The device count equals the number of CPU cores; when the count cannot be determined, fallback_cpu_count is used.

Each device is sized to the same share of memory_fraction_percent of installed RAM, rounded down to the alignment_bytes zram page size.  
Total ZRAM capacity is memory_fraction_percent of installed RAM.

ZRAM should be aggressive, with strong compression, using almost all memory.  
Each device uses the configured compressor algorithm.  
ZRAM swap is activated with the configured swap_priority, so it is used before the disk swapfile.

All parameter values live in the src/pyntara/values/zram_service.py: compressor, swap_priority, memory_fraction_percent, fallback_cpu_count, alignment_bytes, reset_busy_attempts, reset_busy_retry_delay_seconds, meminfo_total_key and cpuinfo_processor_key. The last two name the kernel file lines the task reads: the installed RAM in /proc/meminfo, with the separator that file uses, and the per-core line of /proc/cpuinfo, so a kernel that renames a field is answered in the config.
reset_busy_attempts and reset_busy_retry_delay_seconds bound the retries of a reset or hot_remove that the kernel rejects with EBUSY while a transient opener, for example a udev probe, holds the device.

The zram_service task configures the devices immediately and installs a systemd oneshot service that repeats the setup at every boot.

## Zswap

Zswap is a compressed cache for swap pages: pages that are being swapped out are compressed into a RAM pool before they reach the backing swapfile, trading CPU cycles for reduced swap I/O.
The zswap_service task writes the parameters into the kernel attribute directory named by parameters_dir_path immediately and installs a systemd oneshot service that repeats the writes at every boot.
Zswap requires a backing swap device, so the task depends on swapfile_service_install.

The values are aggressive, matching the ZRAM philosophy. All parameters live in the src/pyntara/values/zswap_service.py: parameters_dir_path, the parameter_names list in the order the task writes them, and the value of every name as the key of the same name (enabled, compressor, max_pool_percent, accept_threshold_percent, shrinker_enabled), plus the unit template name and the service unit name. A parameter the kernel drops is removed from parameter_names; a boolean key is written as the Y/N spelling the attributes report.

## Swap file

Task: swapfile_service_install. One program of the section, deployed from
task_data/swapfile_service_install/configure_swapfile.py to its configured path,
creates, formats and activates the swap file at swapfile_path, which is
/swap/swapfile, and the program creates the directory that holds it when the
directory is not there yet. On a machine whose root is btrfs that directory is
already the mount point of the swap subvolume, which the storage section creates
and mounts (docs/spec/btrfs-setup.md, "The swap area"); on any other machine the
directory stays an ordinary directory, which is what the section needs there.
The unit
swapfile.service starts that same program at every boot, so a run and a boot
apply one code instead of two implementations of the same steps. The packages
the tools of the program come from (mount for swapon and swapoff, util-linux for
mkswap and fallocate, e2fsprogs for chattr) are installed through the shared
package helper, so the section never assumes the machine already carries them.

The size is min(RAM * ram_multiplier + ram_extra_mb, free_disk * disk_fraction).
The installed RAM is read from the line of /proc/meminfo whose name
meminfo_total_key carries, with the separator that file uses, and free disk space
comes from the filesystem that holds the configured swap file. Where the RAM term
wins, the size does not follow the free space of the moment, so a machine with
room to spare keeps its size; where the disk term wins, the file follows the free
space of every run and of every boot. A file whose size deviates from the target
by more than size_tolerance_mb is created again.

One recipe creates the file on every filesystem: the program makes an empty file,
asks for the no-copy-on-write attribute, preallocates the size without holes, sets
the declared mode and writes the swap signature. That order is what a btrfs swap
file requires, because the attribute can be set only while the file holds no data
blocks; on a filesystem without copy-on-write the attribute step is refused, and
the program reports that as a note and continues. The program does not detect the
filesystem type for the recipe, because the same recipe holds everywhere; the
resume offset it reports is the one exception, because the blocks a btrfs swap
file appears to have are not the blocks the kernel resumes with. The path of
every tool is discovered at run time instead of being written down, so a machine
that keeps its tools elsewhere is followed.

The program refuses storage that cannot hold swap. A probe file of probe_size_kb
is created next to the swap file by the same recipe, formatted and activated, and
only a probe the kernel accepts lets the real size be allocated: a swap file on
storage that keeps its data in memory would occupy what it is meant to extend,
and the kernel refuses to activate it in any case. The reason names the step that
failed, so an allocation failure, a formatting failure and a kernel refusal are
told apart. Such a refusal is reported as a warning of a completed task and the
service stays installed, because the storage of the next boot may well be a disk;
the task never stops the run.

The unit carries the command line the task builds from the values, so the program
receives every number as an argument and carries none of its own, and the program
answers with one result line naming what changed, what was refused and what
failed. The unit requires the mount that provides the swap file
(RequiresMountsFor), and it stops the swap through the absolute path of swapoff
that the task resolves at run time, so the boot service takes nothing from PATH.
After the program has run, the task starts the unit, so the artifact of the next
boot is proved by this run.

Known limitation of btrfs: a filesystem that holds an active swap file skips the
block groups of that file during balance and scrub, which the upstream
documentation calls especially undesirable on the root filesystem, and a subvolume
that contains an active swap file cannot be snapshotted.

The section also makes hibernation possible, because a swap file that no resume
address names is a swap file the kernel can never resume from. The address has two
halves, and they are published together: the device that holds the swap file and
the offset of the swap file header inside that device. The kernel reads the device
from the kernel command line of the boot entry and the offset from the same line,
because it ignores the offset of the resume parameter and a device alone does not
name a swap file. The device is read from the fstab line of the mount that holds
the swap file, so it is written the way this machine reaches that filesystem,
which is a UUID for a filesystem that is mounted directly and the mapper path of a
root that is unlocked at boot; the offset is read by the program from the machine
itself, because it moves whenever the swap file is created again. Both halves are
written into the kernel command line of the boot menu and into the attributes of
the running kernel, and the device is written into the file the initramfs reads as
well, so a machine whose command line a person edited by hand still carries it.
The snapshot entries of the btrfs menu copy the current command line when that
menu is generated, so they carry the address as well.

Both settings files are shell variable files, and the section edits them through
augeas: the tool parses the file with the lens the values name, the program
replaces the value of one node of the parsed tree and the tool writes the file
back. Every other line, every comment and the quoting style of the machine
therefore survive, and the section never spells the syntax of a machine file
itself. The kernel command line is one node whose value carries the quotes of the
file, so the words inside that quoting are what changes. The menu is rebuilt only
when the value changed, and the program rebuilds it when it runs at boot as well,
because a boot that created the swap file again carries a new offset. A machine
without the augeas tool gets a warning and keeps its working swap file.

Hibernation needs a permission that Ubuntu refuses to every user through a rule of
its own, so the section writes a polkit rule for the desktop user of this machine
alone. The rule file carries a name that sorts before the rule of that
distribution, because polkit decides by the first rule file in name order that
answers, and it allows the two actions the session menu asks for: the hibernation
and its form for a machine with several open sessions. A machine without a desktop
account gets no rule, because it has no session menu to offer hibernation in.

The task then reads back what it published: the offset parameter of the generated
boot menu, the resume file inside the rebuilt initial ramdisk, and the answer of
the machine about hibernation, asked on behalf of the desktop user, because that
answer is calculated for the account that asks. A step that cannot be read back is
reported as a warning of a completed task, with the reason. The run tells the user
that the item of the session menu appears after the next login, because the shell
asks the machine once, when the session starts.

No argument of the unit command line carries a space, because the unit renders the
command line as words separated by spaces and an argument with a space inside
would arrive at the boot service as two arguments.

All parameter values live in the src/pyntara/values/swapfile_service_install.py:
the swapfile path and mode, the packages of the tools, the size formula factors,
the accepted deviation size_tolerance_mb, the probe size probe_size_kb, the kernel
file the memory is read from, the name of the tool the unit stops the swap with,
the file name, deployed path and mode of the program, the fstab and the settings
files the resume address lives in, the key of the kernel command line and the two
parameter names, the lens augeas parses those files with, the key the initramfs
reads the device from, the mount point query, the commands and bounds that rebuild
the boot artifacts, the permission rule file with its mode, its template and its
two actions, the tool that asks the machine about hibernation, the answer that
means it is offered, the command that runs a call as the desktop user, and the
sentence that tells the user about the next login.

These tasks create system services executed at system startup.
