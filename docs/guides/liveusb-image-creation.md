# Live USB image creation

Baking a Pyntara-configured machine into a live USB image that also offers installation to disk. Walked on 2026-10-07 on Kubuntu 26.04.1 for the export and the rebuild, and on 2026-10-08 and 2026-10-09 for the installer and the read-only route into a stopped machine.

## Source

The desktop ISO of the same release: kubuntu-26.04.1-desktop-amd64.iso, hybrid BIOS plus EFI plus GPT, carrying /casper/filesystem.squashfs, /casper/initrd and /casper/vmlinuz.

## Reading a stopped machine

A stopped domain can be read without booting it and nothing in it is written.

The disk of a domain is a chain of qcow2 layers and the ACTIVE layer is the top one, which libvirt lists first in the domain XML; `qemu-img info` prints the order of the chain. Opening a middle layer as if it were the machine gives "can't read superblock". Measured on test002: `test002.pre-tools-removal` -> `test002.pre-slim` -> `test002.qcow2` -> `clean_with_all_installed_dont_write_on_it.qcow2`.

`qemu-nbd --read-only --connect=/dev/nbdN <active layer>` then mounting the partition works, but the process with `--fork` lives only as long as the command that started it: the session cgroup takes it down, the next command meets "I/O error, dev nbdN, sector 0" in `dmesg` and "bad superblock" from mount, and the partition nodes /dev/nbdNpM stay stale between sessions. The mount option `nologreplay` is not supported by the kernel of this host: `btrfs: Unknown parameter 'nologreplay'`.

The stable route has no daemon: `qemu-img convert -O raw -S 4k <active layer> <raw file>`, then `losetup --read-only -P --show <raw file>`, then mount the subvolume. The loop device and the mount live in the kernel, so they survive between commands; a stale node cannot appear. Measured on test002: 60 GiB of virtual size, 11 GiB of raw file.

The root of a machine installed by the project lies in the subvolume @: mount it with `-o ro,subvol=@`. The top level alone shows only the subvolumes @, @home, @points and @swap. Home is a separate subvolume, `-o ro,subvol=@home`. Keep the whole sequence, from the conversion or the connect to the mount, in one command when using nbd.

Trap: the images directory is mode 0711, so a glob written by the user shell does not expand; write the paths out and use sudo.

## Export

Copy the machine from the read-only mounts, not from the running system: the machine stays off, the copy is consistent, and nothing on it changes. Mount the root subvolume at one point and the home subvolume at another, then copy the root subvolume into the tree and the home subvolume into `<tree>/home`.

Use `rsync --archive --hard-links --acls --xattrs --numeric-ids`. Exclude the CONTENTS of these paths, not the paths themselves, so that the image keeps the directories: /proc, /sys, /dev, /run, /tmp, /mnt, /media, /cdrom, /lost+found, /snap, /boot/efi, /var/cache/apt, /var/lib/apt/lists, /var/lib/snapd/cache, /var/log/journal, /var/tmp and /home, which the second pass fills. A system whose image lacks the /proc or /dev directory does not work.

Nothing has to be excluded for /points and /swap: they are separate subvolumes, invisible from the root subvolume, so the 24 GiB and the 8.6 GiB of test002 never enter the tree. Both appear in the tree as empty directories, which the swap and the btrfs tasks expect.

Deleting the snap seed frees nothing: its files are hardlinked into /var/lib/snapd/snaps.

Log and rate: send the rsync output to a log, `--info=name` gives a line per copied file and `--info=stats2` the transfer statistics, and sample the size of the tree every 30 seconds to log a rate, for example `copied 5430858709 bytes, rate 30.8 MiB/s`. Run the export as a transient unit, `systemd-run --unit=<name> --collect --property=StandardOutput=append:<log> -- /bin/bash <script>`, so that a long copy does not depend on the shell that started it, and watch it with `tail -F -n +1 <log>`; a plain `tail -F` shows only the last ten lines.

Verify the copy with the same rsync in dry-run mode: `--dry-run --itemize-changes` must report nothing but the change time of /home, which the second pass makes by definition. Measured on test002 on 2026-10-09: 169597 entries, 5.8 GiB, 79 seconds, dry run clean.

## Slimming before export

The Export list alone frees little; the packages do. Measured on test002 (Kubuntu 26.04.1, btrfs compress=zstd:15, 3057 packages, 16 GiB used): /swap/swapfile held 8.5 GiB and /var/cache/apt 2.0 GiB, and removing those plus locales except ru and en, /usr/share/doc, libreoffice, texlive, pandoc, calibre, node, java, the build tools, the caches and the logs took the machine to 7.5 GiB. What remains is what makes it work: firmware 735 MiB, kernel modules, the core libraries, the KDE stack, Firefox. Measured again on 2026-10-09: the root subvolume holds 6.2 GiB and 2375 packages.

Never purge a package a kept one depends on. Measured traps on Kubuntu: kf6-breeze-icon-theme and fonts-noto-core take plasma-workspace, plasma-desktop and kubuntu-desktop with them; cpp-15 does the same through x11-xserver-utils and cpp; libllvm21 takes mesa-vulkan-drivers and mesa-libgallium, hence the graphics. Deleting a file a running configuration depends on breaks the session the same way: emptying /usr/share/wallpapers makes the desktop come up black with no panel, because the Plasma configuration points its wallpaper at /usr/share/wallpapers/Kubuntu; a wallpaper package may be purged, the files the configuration references may not be deleted. Simulate every removal first with apt-get -s purge <packages> and read the Purg list for plasma, kde, kwin, sddm, kubuntu, mesa, llvm.

Snapshot before a removal so the step is reversible: virsh snapshot-create-as <domain> <name> --disk-only --atomic, revert with virsh snapshot-revert <domain> <name>.

Measure the result with care: compress=zstd makes du overstate the on-disk cost of a directory, and df does not drop while a running process holds deleted files open (lsof +L1 names them); read the freed size after the session restarts.

## Identities to keep

Don't blank /etc/machine-id. RustDesk decrypts enc_id and password in its RustDesk.toml with a key derived from the machine identity, so a fresh machine-id makes it report a new ID, lose the password and rewrite the file. The same holds for any state encrypted to the machine.

Keep /etc/ssh host keys, /var/lib/tor with its onion keys, /var/lib/i2pd, /var/lib/pyntara and /etc/pyntara/pass.

## Live user and host

casper reads /etc/casper.conf from the INITRAMFS, not from the squashfs, and copies its own copy over the live root at the end of the boot. With an empty FLAVOUR it overwrites USERNAME and HOST with the first word of /cdrom/.disk/info, which is kubuntu.

So set USERNAME, USERFULLNAME, HOST and a non-empty FLAVOUR in /etc/casper.conf inside /casper/initrd, and repack the initrd as its uncompressed microcode cpio plus a gzip-compressed main cpio. Autologin then follows on its own: casper appends an [Autologin] section to /etc/sddm.conf. Verbatim, the tree also yields the machine-id, and the project keeps the live user i: USERNAME="i", USERFULLNAME="i", HOST="i", FLAVOUR="kubuntu".

## Preparing the tree

The installer snap and cloud-init have to be installed INTO the tree, which needs a chroot. The traps below were all met live.

A chroot that binds only /proc, /sys and /dev makes dpkg fail with `E: Can not write log (Is /dev/pts mounted?) - posix_openpt (19: No such device)` and the package install aborts. /dev/pts is a mount of its own, so binding /dev does not bring it; mount the devpts instance on `<tree>/dev/pts` too. This cost the same step twice, on 2026-10-08 and again on 2026-10-09.

DNS inside the chroot: the /etc/resolv.conf of the tree is a symlink to /run/systemd/resolve/stub-resolv.conf, which resolves nothing there, and apt-get update then answers nothing. Create that path and bind the resolv.conf of the host onto it; apt-get update returns 0 with that in place.

Directories apt expects and a squeezed tree can have lost: /var/cache/apt/archives (755 root:root), /var/cache/apt/archives/partial and /var/lib/apt/lists/partial (700 _apt:root). Create them with those modes before entering the chroot, because apt works as the _apt user.

Unmount every bind mount before the tree is archived. A bind mount left under the tree makes mksquashfs walk into the host /proc and /sys and read them without end, while the build looks alive: measured on 2026-10-09, with /proc, /sys and /dev still mounted under the tree, mksquashfs reached 8 TB of characters read, held sysfs files open, its progress counter stood still and its output stayed at 173 MiB for ten minutes. Before mksquashfs, `findmnt -R <tree>` must list nothing; unmount in the reverse order dev/pts, dev, sys, proc.

After the install, clean the tree again: `apt-get clean` inside the chroot, and move the fetched index out of /var/lib/apt/lists, or the squashfs carries about 200 MiB of index that nothing needs.

Reuse instead of download: the installer snap can be copied out of an earlier tree (22 MiB), and the patched initrd of an earlier build can be reused verbatim whenever the casper identity is unchanged (USERNAME, USERFULLNAME, HOST, FLAVOUR), which saves the unpack and the repack of the cpio pair.

## Installer

The installer is subiquity, the one Ubuntu itself uses, taken as a snap from the channel 26.04/stable and carried inside the image. It partitions, formats, mounts, copies the system and installs the bootloader through curtin; the image carries no partitioning code of its own, because curtin cannot create btrfs subvolumes at all and the target layout it does create is the one the autoinstall describes.

Calamares was tried first and abandoned: it dies with SIGSEGV in the job thread on the first Python job of its second batch (localecfg), and it skips every remaining non-emergency job after any failure, which leaves a target without a bootloader.

The tree carries /usr/local/share/pyntara-installer with four files: `run_installer.sh`, the only script of the installer; `subiquity.snap`; `autoinstall.yaml.in`, whose placeholders are the target disk and the size of the root partition, computed by the script from the size of that disk because curtin requires an explicit size and does not take -1; and `install-sources.yaml.in`, the source catalog, whose type is fsimage, so curtin copies the squashfs of the image onto the target instead of installing packages. The tree also carries cloud-init, because the subiquity server runs `cloud-init status --wait` and dies without the program. A desktop entry Install Pyntara system runs `konsole --hold -e sudo /usr/local/share/pyntara-installer/run_installer.sh`, so the window and the text of an error stay open.

What the script does: it lists the disks that can carry the system, numbers them, prints what will be erased, and asks for the number of the target disk, where 0 quits without changing anything. That answer is the only confirmation the whole installation asks for. It then writes the two files, installs the snap with `--dangerous --classic`, starts the server and runs the installer. Nothing else.

The candidates come from `lsblk -dno NAME,TYPE,RO`: every device the kernel reports as a disk and as writable, minus the device the live system runs from, minus the devices too small for the two partitions and the slack. Nothing is filtered by name, so a flash drive, an nvme disk or a memory card of a few gigabytes is offered. The size rule is not decoration: the live session carries eleven zram devices of 333.5 MiB, every one of them writable and reported as a disk, so a menu filtered by writability alone holds twelve entries with the real disk last.

The live image needs snapd, and the machine does not carry it, because the project removes snap: `snap install --dangerous --classic` needs a running snapd. Install the snapd package into the tree in the chroot, which pulls squashfs-tools with it, and remove snapd with /snap from the target after the installation, so the installed system keeps the snap-free state the project builds.

The catalog has to sit at /cdrom/casper/install-sources.yaml, the path subiquity reads, and /cdrom is the read-only ISO, so the script mounts a tmpfs over /cdrom/casper and writes the catalog there. The squashfs it names is taken from a separate read-only mount of the live medium under /run/pyntara-iso, because that tmpfs hides the copy of the directory that the ISO itself carries.

The autoinstall has to sit at /autoinstall.yaml, one of the discovery paths of subiquity. It names the source id, marks no section interactive (`interactive-sections: []`) and powers the machine off when the installation is over (`shutdown: poweroff`), and describes the storage: GPT, a 1 MiB bios_grub partition (curtin skips it on UEFI and creates it for BIOS), a 1 GiB EFI partition, and one btrfs partition with the rest of the disk, mounted as root with `compress=zstd:15,noatime,autodefrag`, which curtin also writes into the fstab of the target. Its keyboard section names the layout explicitly: without it the keyboard step of subiquity runs `setupcon --save-only` with whatever model the live session detected, and a model that comes out empty makes setupcon exit 1 without a message, which aborts the installation after curtin has already finished.

The empty list both removes the confirmation of the installer itself and keeps the storage config in force. The server applies an autoinstall section only to a controller that is not interactive (`apply_autoinstall_config` in server/server.py skips an interactive controller), so an interactive section throws its own configuration away. That is why the standard screens of the installer cannot be combined with this storage layout: the guided layouts of subiquity create ext4 (`guided_direct`), no guided layout carries mount options, and btrfs is reachable only through the manual partition screen, where the person types the options by hand. With an empty list the installer asks nothing of its own, but its client still requires the answer yes on standard input before it applies the configuration (`noninteractive_confirmation` in client/client.py), which the script supplies with `printf 'yes\n' | snap run subiquity`. Measured trap of that line: the pipe closes the standard input of the client, so a failing installation cannot offer its own prompt and the client dies with `EOFError: EOF when reading a line` inside `noninteractive_watch_app_state` (2026-10-09); the failure itself is still readable in /var/log/installer.

The live session asks one thing, the number of the disk. Nothing else is asked, and the installation itself needs no network, which takes one file in the tree: `etc/apt/sources.list.d/cdrom.sources`, the medium as a package source. The file has to be in the deb822 syntax (`Types: deb`, `URIs: file:///cdrom`, `Suites: resolute`, `Components: main restricted`, `Trusted: yes`), because apt decides the syntax by the file name: the one-line `deb ...` form written into a file called `.sources` makes apt 3.2 answer `E: Malformed stanza 1 in source list ... (type)` and `E: The list of sources could not be read` (measured 2026-10-09). One such file fails twice: the mirror probe of the installer cannot read the sources, reverts to an offline installation, and curtin then dies in configure_apt on `in-target apt-get update` with exit status 100. With the deb822 file in place the same command returns 0 with the medium alone and only warns about the signature it cannot verify (NO_PUBKEY 0A0E127F63936AAE), which `Trusted: yes` covers. The file also stops the installer from writing a medium source of its own, and subiquity binds the /cdrom of the live system into the target before it refreshes the index (server/apt.py), so the source is reachable inside the target.

The steps that have to run inside the target after the installation are late commands of the autoinstall and not steps of the script: an unattended installation powers the machine off when it is done, so the script returns far too late to touch the target. Measured: the two late commands remove the medium source from the target and copy the network sources of the payload into it.

Logs: the script writes its own steps to /var/log/pyntara-install.log and keeps the screen output of the installer out of that file, because the installer draws a full screen interface whose escape sequences would fill it. The logs of the installer are /var/log/installer (curtin-install.log, subiquity-server-info.log, subiquity-client-info.log, installer-journal.txt, autoinstall-user-data), both in the live session and in the target. When the installer exits while the target is still mounted, the script copies its own log and, on a failure, /var/log/installer and /var/crash into the target.

Traps of this installer, all met live: the snap installs only with --classic; the report of a failed installation can come out empty, because the subiquity snap bundles python 3.12 and asks apport about /usr/bin/python3.12 while the image and the machine carry python 3.14, so the user sees only "Loading the report failed" and the files in /var/log/installer are what still says what happened; a client in its own window needs that window focused, otherwise the keys sent to the machine go elsewhere.

## Updating an image with later changes

A machine that was used after the export carries settings the tree does not have, and the tree can take them without a second export of the whole disk.

Read the machine read-only and diff it against the tree. `guestmount` reaches a stopped domain in one step: `guestmount -a <active layer> -m /dev/sda2:/:subvolid=5 --ro <dir>`, where the mount option belongs to the `-m` specification, because `-o` hands its options to FUSE and rejects `subvolid=5`, and `-i` refuses this disk with "multi-boot operating systems are not supported" (measured 2026-10-09). The top level shows @, @home, @points and @swap at once, so the root subvolume and the home subvolume are both readable through one mount; the raw file plus loop route above is faster and stays the way for a full export.

Ask the machine what changed instead of copying it: `find <mount>/@ -xdev -newermt '<export time>' -type f` and the same over `@home`, with the same exclusions as the export, and diff the configuration directories against the tree (`diff -rq` over `home/i/.config`, `home/i/.local/share`, `etc/xdg`). Copy only the files the diff names, then rebuild the squashfs and the ISO as below.

Measured example, 2026-10-09: a machine installed from an earlier image held the permission that keeps KDE from asking for every new remote screen session in `home/i/.local/share/flatpak/db/screencast`, a file the tree did not have, while the RustDesk configuration of that machine differed only in machine data. The two files show the rule: copy the state that records a decision, never the state that carries the identity of the other machine (the encrypted identity, the permanent password and the address of that machine).

## Rebuild

xorriso -indev ISO -outdev OUT -boot_image any replay -map filesystem.squashfs /casper/filesystem.squashfs -map initrd /casper/initrd -map md5sum.txt /md5sum.txt -commit keeps the BIOS El Torito entry, the hidden EFI image and the GPT.

Update both changed lines of /md5sum.txt. xorriso extracts that file read-only and root-owned, so write the new one through tee.

zstd at -Xcompression-level 19

Build the squashfs as root: run by an ordinary user, mksquashfs cannot read /var/lib/snapd and silently leaves it out of the image (measured: 1.7 MiB instead of 2.45 GiB). Write it on a normal filesystem and not in /tmp, which is a tmpfs of 7.7 GiB on this host and makes mksquashfs die with a fatal error before the end. Check the exit status of mksquashfs and read the log instead of piping it away: a piped run hides both the error and the status, which is how a truncated image was built twice. Measured on 2026-10-08: a tree of 12 GiB gave 6.7 GiB of squashfs at zstd level 1 and an ISO of 7.4 GiB. Measured on 2026-10-09 on the slimmed tree: 6.0 GiB of tree gave 2.58 GiB of squashfs, 44.7 per cent of 5.9 GiB uncompressed, with 172479 inodes and 122436 files, and the run took about two minutes on eight processors at the default zstd level.

## Verify

Boot in QEMU. The key exported with the tree logs into the live session, which then names its host, user, groups, sessions and services; only that shows the session equal to the machine.

Checks that have caught real defects: the size and the file count of the squashfs against the tree, the boot record of the rebuilt ISO (El Torito, the hidden EFI image and the GPT), the md5sum lines inside the ISO, the payload in /usr/local/share/pyntara-installer of the live session, and then a full installation in a virtual machine: the fstab of the target, the mount options of its root, the files of the ESP, and a boot of the installed system with its services active. Mount the produced squashfs read-only and look inside before it becomes an ISO: /usr/bin/snap, the four files of /usr/local/share/pyntara-installer, the wallpapers the Plasma configuration names, and /proc, /sys and /dev as empty directories. A tree that lost /var/lib/snapd and a tree that lost /proc both looked complete until the image was mounted and its directories were counted.
