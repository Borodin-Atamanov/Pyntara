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

The tree carries /usr/local/share/pyntara-installer with the files of the installer: `run_installer.sh`, the only script of the installer; `subiquity.snap`; `autoinstall.yaml.in`, whose placeholders are the target disk, the size of the root partition and the encryption passphrase, all filled by the script because curtin requires an explicit root size and does not take -1 and the passphrase must come from a file rather than the script; `install-sources.yaml.in`, the source catalog, whose type is fsimage, so curtin copies the squashfs of the image onto the target instead of installing packages; `luks-passphrase`, the passphrase of the encrypted root; and `apt-sources/`, the network package sources copied into the target. The tree also carries cloud-init, because the subiquity server runs `cloud-init status --wait` and dies without the program. A desktop entry Install Pyntara system runs `konsole --hold -e sudo /usr/local/share/pyntara-installer/run_installer.sh`, so the window and the text of an error stay open.

What the script does: it lists the disks that can carry the system, numbers them, prints what will be erased, and asks for the number of the target disk, where 0 quits without changing anything. That answer is the only confirmation the whole installation asks for. It then writes the two files, installs the snap with `--dangerous --classic`, starts the server and runs the installer. Nothing else.

The candidates come from `lsblk -dno NAME,TYPE,RO`: every device the kernel reports as a disk and as writable, minus the device the live system runs from, minus the devices too small for the two partitions and the slack. Nothing is filtered by name, so a flash drive, an nvme disk or a memory card of a few gigabytes is offered. The size rule is not decoration: the live session carries eleven zram devices of 333.5 MiB, every one of them writable and reported as a disk, so a menu filtered by writability alone holds twelve entries with the real disk last.

The live image needs snapd, and the machine does not carry it, because the project removes snap: `snap install --dangerous --classic` needs a running snapd. Install the snapd package into the tree in the chroot, which pulls squashfs-tools with it, and remove snapd with /snap from the target after the installation, so the installed system keeps the snap-free state the project builds.

The catalog has to sit at /cdrom/casper/install-sources.yaml, the path subiquity reads, and /cdrom is the read-only ISO, so the script mounts a tmpfs over /cdrom/casper and writes the catalog there. The squashfs it names is taken from a separate read-only mount of the live medium under /run/pyntara-iso, because that tmpfs hides the copy of the directory that the ISO itself carries.

The autoinstall has to sit at /autoinstall.yaml, one of the discovery paths of subiquity. It names the source id, marks no section interactive (`interactive-sections: []`), and describes the storage: GPT, a 1 MiB bios_grub partition (curtin skips it on UEFI and creates it for BIOS), a 1 GiB EFI partition, a 1 GiB ext4 partition mounted at /boot, and the rest of the disk as one LUKS2 partition that holds a btrfs root mounted with `compress=zstd:15,noatime,autodefrag`, written into the fstab of the target. It carries no `shutdown` key, and it must not: the schema of this subiquity allows only `reboot` or `poweroff`, the default is `reboot`, and a `poweroff` (which the first builds carried) leaves the user with a dark screen and no result, which is wrong for a machine whose whole point is a working system. Its keyboard section names the layout explicitly: without it the keyboard step of subiquity runs `setupcon --save-only` with whatever model the live session detected, and a model that comes out empty makes setupcon exit 1 without a message, which aborts the installation after curtin has already finished.

The empty list both removes the confirmation of the installer itself and keeps the storage config in force. The server applies an autoinstall section only to a controller that is not interactive (`apply_autoinstall_config` in server/server.py skips an interactive controller), so an interactive section throws its own configuration away. That is why the standard screens of the installer cannot be combined with this storage layout: the guided layouts of subiquity create ext4 (`guided_direct`), no guided layout carries mount options, and btrfs is reachable only through the manual partition screen, where the person types the options by hand. With an empty list the installer asks nothing of its own, but its client still requires the answer yes on standard input before it applies the configuration (`noninteractive_confirmation` in client/client.py), which the script supplies with `printf 'yes\n' | snap run subiquity`. Measured trap of that line: the pipe closes the standard input of the client, so a failing installation cannot offer its own prompt and the client dies with `EOFError: EOF when reading a line` inside `noninteractive_watch_app_state` (2026-10-09); the failure itself is still readable in /var/log/installer.

The live session asks one thing, the number of the disk. Nothing else is asked, and the installation itself needs no network, which takes one file in the tree: `etc/apt/sources.list.d/cdrom.sources`, the medium as a package source. The file has to be in the deb822 syntax (`Types: deb`, `URIs: file:///cdrom`, `Suites: resolute`, `Components: main restricted`, `Trusted: yes`), because apt decides the syntax by the file name: the one-line `deb ...` form written into a file called `.sources` makes apt 3.2 answer `E: Malformed stanza 1 in source list ... (type)` and `E: The list of sources could not be read` (measured 2026-10-09). One such file fails twice: the mirror probe of the installer cannot read the sources, reverts to an offline installation, and curtin then dies in configure_apt on `in-target apt-get update` with exit status 100. With the deb822 file in place the same command returns 0 with the medium alone and only warns about the signature it cannot verify (NO_PUBKEY 0A0E127F63936AAE), which `Trusted: yes` covers. The file also stops the installer from writing a medium source of its own, and subiquity binds the /cdrom of the live system into the target before it refreshes the index (server/apt.py), so the source is reachable inside the target.

The steps that touch the target after its files are in place are late commands of the autoinstall and not steps of the script, because the installer runs on its own and the script cannot wait for it. Measured: they remove the medium source from the target, remove the apt file that kept the update step from running, copy the network sources of the payload in, write the encryption key file onto /boot, point the crypttab at it, tell the initramfs to carry it and rebuild the initramfs, so the installed machine starts without asking for the passphrase.

Logs: the script writes its own steps to /var/log/pyntara-install.log and keeps the screen output of the installer out of that file, because the installer draws a full screen interface whose escape sequences would fill it. The logs of the installer are /var/log/installer (curtin-install.log, subiquity-server-info.log, subiquity-client-info.log, installer-journal.txt, autoinstall-user-data), both in the live session and in the target. When the installer exits while the target is still mounted, the script copies its own log and, on a failure, /var/log/installer and /var/crash into the target.

Traps of this installer, all met live: the snap installs only with --classic; the report of a failed installation can come out empty, because the subiquity snap bundles python 3.12 and asks apport about /usr/bin/python3.12 while the image and the machine carry python 3.14, so the user sees only "Loading the report failed" and the files in /var/log/installer are what still says what happened; a client in its own window needs that window focused, otherwise the keys sent to the machine go elsewhere.

## Disk encryption with automatic unlock

The image encrypts the system by default: the root is a LUKS2 volume and the machine starts without asking for a passphrase. This was built and verified live on 2026-10-09.

The layout is the one the stock encrypted installer of Ubuntu uses: an unencrypted ESP, an unencrypted ext4 /boot, and the rest of the disk as one LUKS2 partition with a btrfs root on it. The separate unencrypted /boot is what makes the automatic unlock possible: GRUB has to read the kernel and the initramfs before anything is decrypted, and if /boot sat inside the encrypted root GRUB would have to ask for the passphrase first, so no key file could help because the key file itself would be inside the encrypted root.

The passphrase lives in a file, not in the script: the file `luks-passphrase` in the payload (27 bytes, the text `sudo -n cp pyntara-live.iso`, with no trailing newline, because a trailing byte would become part of the passphrase) is read by `run_installer.sh` and substituted into the autoinstall.

curtin creates the LUKS2 volume with that passphrase (the `dm_crypt` of the storage config is LUKS2 because that is the cryptsetup default) and writes a crypttab whose key is `none`, meaning it asks for the passphrase; this holds both for a passphrase and for a keyfile (`dm_crypt_handler` in curtin/commands/block_meta.py). The automatic unlock is therefore added by late commands, the only place that runs after curtin has written the target: they copy the passphrase file to /boot/pyntara.tmp, change the `none` of the crypttab entry to that path, append `KEYFILE_PATTERN=/boot/pyntara.tmp` to /etc/cryptsetup-initramfs/conf-hook, and rebuild the initramfs with `update-initramfs -u -k all`. The cryptroot hook of initramfs-tools reads KEYFILE_PATTERN and copies the key file into the initramfs (usr/share/initramfs-tools/hooks/cryptroot), so the initramfs itself carries the passphrase at boot; the hook and the rebuild are needed for exactly that reason, a crypttab line alone is not enough. Measured on the installed machine: the crypttab entry is `cryptroot UUID=... /boot/pyntara.tmp luks`, /boot/pyntara.tmp holds the passphrase, the root is /dev/mapper/cryptroot, and the machine boots to the desktop with `cryptsetup: cryptroot: set up successfully` and no prompt.

The passphrase file is the temporary weakness that was asked for: whoever can read the unencrypted /boot has the key. To remove it the user adds their own passphrase (`cryptsetup luksAddKey /dev/vda4`), removes the old one (`cryptsetup luksRemoveKey`, or `luksKillSlot` with its number), deletes /boot/pyntara.tmp, drops the key file from the crypttab entry and from conf-hook, and rebuilds the initramfs; the next boot then asks for the passphrase.

## The installer does not run package updates, does not add a swap file, and does not power off

Three behaviours of subiquity had to be turned off, all measured on 2026-10-09.

Package updates. subiquity runs `postinstall/run_unattended_upgrades`, which starts `unattended-upgrades` inside the target; it downloaded and would install about 130 packages (bluez, libc6, openssl, network-manager, polkit and more) over several silent minutes, changing the very configuration the image exists to carry. The step runs only when the installer sees a network (`if self.model.network.has_network` in server/controllers/install.py), so on a machine without network it is skipped and the same image behaves differently. This build of subiquity has no way to say no updates: the `updates` key accepts only `security` or `all`, and `security` is the default. The tree therefore carries an apt configuration file, /etc/apt/apt.conf.d/zzzzz-pyntara-no-auto-upgrades, that blacklists every package from that step; the name sorts after the file subiquity writes for the step, so it is read last and wins. Trap: the blacklist entries are regular expressions, so the pattern must be `.*` and not `*`; `*` is an invalid regex and crashes the step with `nothing to repeat at position 0` (measured), while `.*` makes the step list every package as blacklisted and return at once. The installer removes the file from the target in a late command, so the installed system keeps the usual behaviour.

The swap file. curtin creates /swap.img by default when subiquity thinks a swap file should be added (server/models/filesystem.py, method render: with no `swap` in the storage config and a root that can carry a swap file it says nothing to curtin, and curtin applies its own default). The project installs its own swap file (the swapfile_service_install task, /swap/swapfile) and zram, so this is a second, unwanted swap. `storage.swap.size: 0` in the autoinstall tells curtin not to create any, and the installed machine then shows only /swap/swapfile and zram.

Logs that say what is happening. The installer prints only its own top level steps, so a long step such as curthooks leaves the window empty for minutes and the installation looks stopped. The script therefore tails /var/log/installer/curtin-install.log to the console while it runs, next to its own periodic line `Xs elapsed, N MiB written to the target`, so the window always shows what curtin is doing.

The power off. There is no `shutdown` key, so subiquity reboots and the user sees the installed system start instead of a dark screen. A machine that reboots with the medium still first in the boot order starts the live session again, so a test that must show the installed system sets the disk first.

## Updating an image with later changes

A machine that was used after the export carries settings the tree does not have, and the tree can take them without a second export of the whole disk.

Read the machine read-only and diff it against the tree. `guestmount` reaches a stopped domain in one step: `guestmount -a <active layer> -m /dev/sda2:/:subvolid=5 --ro <dir>`, where the mount option belongs to the `-m` specification, because `-o` hands its options to FUSE and rejects `subvolid=5`, and `-i` refuses this disk with "multi-boot operating systems are not supported" (measured 2026-10-09). The top level shows @, @home, @points and @swap at once, so the root subvolume and the home subvolume are both readable through one mount; the raw file plus loop route above is faster and stays the way for a full export.

Ask the machine what changed instead of copying it: `find <mount>/@ -xdev -newermt '<export time>' -type f` and the same over `@home`, with the same exclusions as the export, and diff the configuration directories against the tree (`diff -rq` over `home/i/.config`, `home/i/.local/share`, `etc/xdg`). Copy only the files the diff names, then rebuild the squashfs and the ISO as below.

Measured example, 2026-10-09: a machine installed from an earlier image met the screen sharing question of the KDE portal on every new remote session, and the answer to it is a pair of files rather than one. The portal record lives in `home/i/.local/share/flatpak/db/screencast` and is keyed by the session token, and the client keeps that same token in `home/i/.config/rustdesk/RustDesk_local.toml` as `wayland-restore-token`. Both files were taken from a machine where the dialog had been answered, and the connection to the rebuilt image then started with no dialog (docs/spec/rustdesk-setup.md, Screen sharing without a dialog). A store record whose token no client presents is worth nothing: the first attempt carried the store file alone, with a token the client of that machine did not hold, and the dialog stayed.

## Rebuild

xorriso -indev ISO -outdev OUT -boot_image any replay -map filesystem.squashfs /casper/filesystem.squashfs -map initrd /casper/initrd -map md5sum.txt /md5sum.txt -commit keeps the BIOS El Torito entry, the hidden EFI image and the GPT.

Update both changed lines of /md5sum.txt. xorriso extracts that file read-only and root-owned, so write the new one through tee.

zstd at -Xcompression-level 19

Build the squashfs as root: run by an ordinary user, mksquashfs cannot read /var/lib/snapd and silently leaves it out of the image (measured: 1.7 MiB instead of 2.45 GiB). Write it on a normal filesystem and not in /tmp, which is a tmpfs of 7.7 GiB on this host and makes mksquashfs die with a fatal error before the end. Check the exit status of mksquashfs and read the log instead of piping it away: a piped run hides both the error and the status, which is how a truncated image was built twice. Measured on 2026-10-08: a tree of 12 GiB gave 6.7 GiB of squashfs at zstd level 1 and an ISO of 7.4 GiB. Measured on 2026-10-09 on the slimmed tree: 6.0 GiB of tree gave 2.58 GiB of squashfs, 44.7 per cent of 5.9 GiB uncompressed, with 172479 inodes and 122436 files, and the run took about two minutes on eight processors at the default zstd level.

## Verify

Boot in QEMU. The key exported with the tree logs into the live session, which then names its host, user, groups, sessions and services; only that shows the session equal to the machine.

Checks that have caught real defects: the size and the file count of the squashfs against the tree, the boot record of the rebuilt ISO (El Torito, the hidden EFI image and the GPT), the md5sum lines inside the ISO, the payload in /usr/local/share/pyntara-installer of the live session, and then a full installation in a virtual machine: the fstab of the target, the mount options of its root, the files of the ESP, and a boot of the installed system with its services active. Mount the produced squashfs read-only and look inside before it becomes an ISO: /usr/bin/snap, the four files of /usr/local/share/pyntara-installer, the wallpapers the Plasma configuration names, and /proc, /sys and /dev as empty directories. A tree that lost /var/lib/snapd and a tree that lost /proc both looked complete until the image was mounted and its directories were counted.

## Installer payload files

The build copies these files into the tree at /usr/local/share/pyntara-installer, and the apt file into /etc/apt/apt.conf.d, so the image can be rebuilt from this record alone. This is the payload of the build that produced pyntara-live-2026-10-09n.iso on 2026-10-09.

Rebuild commands:

```sh
cd /home/i/Downloads/pyntara-iso
sudo cp installer-payload/run_installer.sh installer-payload/autoinstall.yaml.in installer-payload/luks-passphrase root-test002/usr/local/share/pyntara-installer/
sudo chmod 755 root-test002/usr/local/share/pyntara-installer/run_installer.sh
sudo cp installer-payload/apt-conf.d/zzzzz-pyntara-no-auto-upgrades root-test002/etc/apt/apt.conf.d/
sudo mksquashfs root-test002 build/filesystem.squashfs -comp zstd -b 131072 -noappend -processors 8
newmd5=$(md5sum build/filesystem.squashfs | awk '{print $1}')
sed "s|^[0-9a-f]\{32\}  \./casper/filesystem\.squashfs\$|$newmd5  ./casper/filesystem.squashfs|" previous-md5sum.txt > build/md5sum.txt
sudo xorriso -indev previous.iso -outdev pyntara-live.iso -boot_image any replay -map build/filesystem.squashfs /casper/filesystem.squashfs -map build/md5sum.txt /md5sum.txt -commit
```

run_installer.sh:

```bash
#!/bin/bash
# Starts the Ubuntu installer with the configuration carried by this image.
# The script asks which disk may be erased, writes the two files the installer
# reads and runs the installer. Partitioning, formatting, mounting with
# compression, copying the system and the bootloader are done by the installer.
set -euo pipefail

PAYLOAD_DIR=/usr/local/share/pyntara-installer
SNAP_FILE="$PAYLOAD_DIR/subiquity.snap"
CATALOG_TEMPLATE="$PAYLOAD_DIR/install-sources.yaml.in"
AUTOINSTALL_TEMPLATE="$PAYLOAD_DIR/autoinstall.yaml.in"
LOCAL_LOG=/var/log/pyntara-install.log
SERVER_LOG=/var/log/pyntara-install-server.log
CLIENT_LOG=/var/log/pyntara-install-client.log
CATALOG=/cdrom/casper/install-sources.yaml
AUTOINSTALL=/autoinstall.yaml
IMAGE_MOUNT=/run/pyntara-iso
BIOS_PARTITION_BYTES=1048576
EFI_PARTITION_BYTES=1073741824
BOOT_PARTITION_BYTES=1073741824
SIZE_SLACK_BYTES=8388608

say() { printf '%s\n' "$*"; }

report_failure() {
    stop_progress_reporter
    say ""
    say "The installer stopped before finishing."
    say "The log of this step is $LOCAL_LOG"
}
trap report_failure ERR

PROGRESS_REPORTER_PID=""
DETAIL_TAIL_PID=""
stop_progress_reporter() {
    [ -n "$PROGRESS_REPORTER_PID" ] && kill "$PROGRESS_REPORTER_PID" 2>/dev/null || true
    PROGRESS_REPORTER_PID=""
    [ -n "$DETAIL_TAIL_PID" ] && kill "$DETAIL_TAIL_PID" 2>/dev/null || true
    DETAIL_TAIL_PID=""
}

# The unattended installer prints few lines of its own, so a window that shows
# only those looks dead while the copy runs for minutes. This reporter prints how
# much has reached the target, often enough that the person in front of the
# machine sees the work moving.
report_progress() {
    local start_time current_time written
    start_time=$(date +%s)
    while sleep 15; do
        current_time=$(date +%s)
        if mountpoint -q /target; then
            written=$(du -sm /target 2>/dev/null | cut -f1)
            say "$((current_time - start_time))s elapsed, $written MiB written to the target"
        else
            say "$((current_time - start_time))s elapsed, the installer is working"
        fi
    done
}

exec > >(tee -a "$LOCAL_LOG") 2>&1

if [ "$(id -u)" -ne 0 ]; then
    say "This must run as root."
    exit 1
fi

# One run at a time. A second window would collide with the snap install of the
# first and report a failure of its own, which reads like a broken installer.
exec 9> /run/pyntara-installer.lock
if ! flock -n 9; then
    say "Another installer is already running; its window shows the progress."
    exit 1
fi

say "Pyntara image installer."
say "The installation itself is done by the Ubuntu installer; this step only"
say "chooses the target disk and starts it."

LIVE_SOURCE=$(findmnt -no SOURCE /cdrom)
LIVE_DISK=$(printf '%s' "$LIVE_SOURCE" | sed -E 's|p?[0-9]+$||')
say ""
say "This live system runs from $LIVE_SOURCE, so $LIVE_DISK is not a candidate."
say ""
# A disk can carry the installation when the kernel reports it as a disk and as
# writable, which is the same condition the installer itself relies on. Nothing
# is chosen by name, so flash drives and other removable media are listed too.
# Devices that cannot hold the system partitions are left out: in this image the
# memory backed disks are writable but hold only a few hundred megabytes.
CANDIDATES=$(lsblk -dno NAME,TYPE,RO | awk '$2 == "disk" && $3 == "0" {print "/dev/" $1}' | grep -v -x "$LIVE_DISK" || true)

say "The installer erases the whole disk chosen below. It creates a 1G EFI"
say "partition, a 1G boot partition and a btrfs partition that holds this"
say "system, encrypts the system partition with LUKS2, copies the system onto"
say "it and installs the bootloader. The machine starts without asking for the"
say "passphrase, which is kept in /boot/pyntara.tmp on the installed system."
say "Every file on the chosen disk is lost and cannot be recovered."
say ""
say "Disks that can carry this system:"
INSTALLABLE_DISKS=""
DISK_NUMBER=0
for candidate_disk in $CANDIDATES; do
    candidate_root_bytes=$(($(blockdev --getsize64 "$candidate_disk") - BIOS_PARTITION_BYTES - EFI_PARTITION_BYTES - BOOT_PARTITION_BYTES - SIZE_SLACK_BYTES))
    if [ "$candidate_root_bytes" -le 0 ]; then
        continue
    fi
    INSTALLABLE_DISKS="$INSTALLABLE_DISKS $candidate_disk"
    DISK_NUMBER=$((DISK_NUMBER + 1))
    say "  $DISK_NUMBER) $candidate_disk $(lsblk -dno SIZE "$candidate_disk")"
done
if [ "$DISK_NUMBER" -eq 0 ]; then
    say "No disk is large enough to carry this system."
    exit 1
fi
say ""
while true; do
    read -r -p "Number of the disk to erase and install onto, or 0 to quit: " DISK_CHOICE
    if printf '%s' "$DISK_CHOICE" | grep -qE '^[0-9]+$'; then
        if [ "$DISK_CHOICE" -eq 0 ]; then
            say "Nothing was changed."
            exit 1
        fi
        TARGET_DISK=$(printf '%s\n' $INSTALLABLE_DISKS | sed -n "${DISK_CHOICE}p")
        if [ -n "$TARGET_DISK" ]; then
            break
        fi
    fi
    say "Type one of the numbers listed above, or 0 to quit."
done

DISK_BYTES=$(blockdev --getsize64 "$TARGET_DISK")
ROOT_SIZE_BYTES=$((DISK_BYTES - BIOS_PARTITION_BYTES - EFI_PARTITION_BYTES - BOOT_PARTITION_BYTES - SIZE_SLACK_BYTES))
say "Target disk $TARGET_DISK, encrypted btrfs system partition of $ROOT_SIZE_BYTES bytes."

say ""
say "Reading the system image from $LIVE_SOURCE."
mkdir -p "$IMAGE_MOUNT"
mountpoint -q "$IMAGE_MOUNT" || mount -o ro "$LIVE_SOURCE" "$IMAGE_MOUNT"
SQUASHFS="$IMAGE_MOUNT/casper/filesystem.squashfs"
if [ ! -f "$SQUASHFS" ]; then
    say "The system image $SQUASHFS was not found."
    exit 1
fi

mountpoint -q /cdrom/casper || mount -t tmpfs tmpfs /cdrom/casper
sed -e "s|@SQUASHFS_PATH@|$SQUASHFS|" -e "s|@SQUASHFS_SIZE@|$(stat -c %s "$SQUASHFS")|" \
    "$CATALOG_TEMPLATE" > "$CATALOG"
say "System source written to $CATALOG."
if [ ! -f "$PAYLOAD_DIR/luks-passphrase" ]; then
    say "The passphrase file $PAYLOAD_DIR/luks-passphrase is missing."
    exit 1
fi
LUKS_PASSPHRASE=$(cat "$PAYLOAD_DIR/luks-passphrase")
sed -e "s|@TARGET_DISK@|$TARGET_DISK|" -e "s|@ROOT_SIZE_BYTES@|$ROOT_SIZE_BYTES|" \
    -e "s|@LUKS_PASSPHRASE@|$LUKS_PASSPHRASE|" \
    "$AUTOINSTALL_TEMPLATE" > "$AUTOINSTALL"
say "Installation configuration written to $AUTOINSTALL."

systemctl is-active --quiet snapd || systemctl start snapd

if ! snap list subiquity > /dev/null 2>&1; then
    say "Installing the installer from the image."
    snap install --dangerous --classic "$SNAP_FILE" 2>&1
fi
snap stop --disable subiquity 2>&1 || true

say ""
say "Starting the installer. It asks nothing and shows its progress until the"
say "machine powers off. Detailed logs will be in /var/log/installer."
say ""
setsid snap run subiquity.subiquity-server > "$SERVER_LOG" 2>&1 &
for _ in $(seq 1 60); do
    [ -S /run/subiquity/socket ] && break
    sleep 1
done
if [ ! -S /run/subiquity/socket ]; then
    say "The installer did not start; see $SERVER_LOG."
    exit 1
fi

# Everything the installer prints goes to the console and into its own log at the
# same time, so no part of the run is lost when the window is closed or read
# later: the log is the whole record of what the installer said.
exec > >(tee -a "$CLIENT_LOG") 2>&1

# The installer prints only its own top level steps, so a long step leaves the
# window empty for minutes and the installation looks stopped. The detailed log
# of what curtin runs is shown beside those steps, so the window always says
# what is happening.
tail -F -n +1 /var/log/installer/curtin-install.log &
DETAIL_TAIL_PID=$!
report_progress &
PROGRESS_REPORTER_PID=$!

# The installer asks on its own input whether the configuration must be applied.
# This script answers it, so the choice of the disk above stays the only
# confirmation the user gives.
INSTALL_EXIT=0
printf 'yes\n' | snap run subiquity || INSTALL_EXIT=$?
printf '%s\n' "The installer exited with status $INSTALL_EXIT." >> "$LOCAL_LOG"
stop_progress_reporter

say ""
if [ "$INSTALL_EXIT" -ne 0 ]; then
    say "The installer stopped with status $INSTALL_EXIT."
    say "The installer logs are /var/log/installer and /var/crash."
    for log_file in /var/log/installer/subiquity-server-info.log "$SERVER_LOG" "$CLIENT_LOG"; do
        if [ -s "$log_file" ]; then
            say ""
            say "Last lines of $log_file:"
            tail -n 20 "$log_file"
            break
        fi
    done
fi
if mountpoint -q /target; then
    mkdir -p /target/var/log/installer
    cp -a "$LOCAL_LOG" /target/var/log/installer/pyntara-install.log 2>/dev/null || true
    cp -a "$CLIENT_LOG" /target/var/log/installer/pyntara-install-client.log 2>/dev/null || true
    cp -a "$SERVER_LOG" /target/var/log/installer/pyntara-install-server.log 2>/dev/null || true
    if [ "$INSTALL_EXIT" -ne 0 ]; then
        cp -a /var/log/installer/. /target/var/log/installer/ 2>/dev/null || true
        mkdir -p /target/var/crash
        cp -a /var/crash/. /target/var/crash/ 2>/dev/null || true
    fi
    say "The log of this step, together with the installer logs, is in"
    say "/var/log/installer of the installed system."
fi
say "Log of this step: $LOCAL_LOG"
say "Output of the installer: $CLIENT_LOG and $SERVER_LOG"
say "Logs of the installer: /var/log/installer"

```

autoinstall.yaml.in:

```yaml
version: 1
# An empty list makes the installer ask nothing at all: it starts the
# installation on its own and never shows its own destructive action
# confirmation. The only confirmation of this installer is the numbered
# choice of the target disk in run_installer.sh.
interactive-sections: []
keyboard:
  layout: us
source:
  id: pyntara
storage:
  version: 1
  # The project already installs its own swap file (the swapfile_service_install
  # task) and zram, so the installer must not add a second swap of its own. A
  # size of zero tells curtin not to create the swap file it would otherwise
  # create by default.
  swap:
    size: 0
  config:
    - id: target-disk
      type: disk
      path: @TARGET_DISK@
      ptable: gpt
      preserve: false
      wipe: superblock-recursive
      grub_device: true
    - id: bios-boot-partition
      type: partition
      device: target-disk
      number: 1
      size: 1M
      flag: bios_grub
      preserve: false
      wipe: superblock
    - id: efi-partition
      type: partition
      device: target-disk
      number: 2
      size: 1G
      flag: boot
      grub_device: true
      preserve: false
      wipe: superblock
    - id: efi-format
      type: format
      fstype: fat32
      volume: efi-partition
      label: EFI
      preserve: false
    - id: boot-partition
      type: partition
      device: target-disk
      number: 3
      size: 1G
      preserve: false
      wipe: superblock
    - id: boot-format
      type: format
      fstype: ext4
      volume: boot-partition
      label: boot
      preserve: false
    - id: boot-mount
      type: mount
      path: /boot
      device: boot-format
    - id: root-partition
      type: partition
      device: target-disk
      number: 4
      size: @ROOT_SIZE_BYTES@
      preserve: false
      wipe: superblock
    - id: root-crypt
      type: dm_crypt
      volume: root-partition
      dm_name: cryptroot
      key: @LUKS_PASSPHRASE@
      preserve: false
    - id: root-format
      type: format
      fstype: btrfs
      volume: root-crypt
      label: pyntara
      preserve: false
    - id: root-mount
      type: mount
      path: /
      device: root-format
      options: compress=zstd:15,noatime,autodefrag
    - id: efi-mount
      type: mount
      path: /boot/efi
      device: efi-format
# Late commands run inside the target after its files are in place, which the
# script that drives the installer cannot wait for. They drop the medium as a
# package source, drop the file that kept the installer from running update
# steps, put back the network sources, and set up the automatic unlock of the
# encrypted root: the key file is copied onto the unencrypted /boot, the
# crypttab of the installer is pointed at it, the initramfs is told to carry it
# and is rebuilt so the machine starts without asking for the passphrase.
late-commands:
  - curtin in-target -- rm -f /etc/apt/sources.list.d/cdrom.sources
  - curtin in-target -- rm -f /etc/apt/apt.conf.d/zzzzz-pyntara-no-auto-upgrades
  - curtin in-target -- cp -a /usr/local/share/pyntara-installer/apt-sources/. /etc/apt/sources.list.d/
  - curtin in-target -- cp -a /usr/local/share/pyntara-installer/luks-passphrase /boot/pyntara.tmp
  - curtin in-target -- sed -i 's| none luks| /boot/pyntara.tmp luks|' /etc/crypttab
  - curtin in-target -- sh -c 'echo KEYFILE_PATTERN=/boot/pyntara.tmp >> /etc/cryptsetup-initramfs/conf-hook'
  - curtin in-target -- update-initramfs -u -k all

```

install-sources.yaml.in:

```yaml
version: 1
sources:
  - id: pyntara
    variant: desktop
    name:
      en: Pyntara image
    description:
      en: The configured system carried by this image
    type: fsimage
    path: @SQUASHFS_PATH@
    size: @SQUASHFS_SIZE@
    default: true
kernel:
  default: linux-generic

```

zzzzz-pyntara-no-auto-upgrades:

```
# The image carries an already configured system and this configuration is what
# the build tests, so the installation must not change it with package updates
# it never verified. subiquity itself writes a file for its update step inside
# the target and that step then runs unattended-upgrades there; this file is
# read after that one and blocks every package from that step, so the step finds
# nothing to change and returns at once. The installer removes this file from
# the target when it is done, so the installed system keeps the usual behaviour.
Unattended-Upgrade::Package-Blacklist { ".*"; };

```

luks-passphrase (27 bytes, the text below with no trailing newline):

```
sudo -n cp pyntara-live.iso
```
