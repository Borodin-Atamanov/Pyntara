# Live USB image creation

Baking a Pyntara-configured machine into a live USB image that also offers installation to disk. Walked on 2026-10-07 on Kubuntu 26.04.1 for the export and the rebuild, and on 2026-10-08 and 2026-10-09 for the installer and the read-only route into a stopped machine.

## Source

The desktop ISO of the same release: kubuntu-26.04.1-desktop-amd64.iso, hybrid BIOS plus EFI plus GPT, carrying /casper/filesystem.squashfs, /casper/initrd and /casper/vmlinuz.

## Reading a stopped machine

A stopped domain can be read without booting it and nothing in it is written.

The disk of a domain is a chain of qcow2 layers and the ACTIVE layer is the top one, which libvirt lists first in the domain XML; `qemu-img info` prints the order of the chain. Opening a middle layer as if it were the machine gives "can't read superblock". Measured on test002: `test002.pre-tools-removal` -> `test002.pre-slim` -> `test002.qcow2` -> `clean_with_all_installed_dont_write_on_it.qcow2`.

`qemu-nbd --read-only --connect=/dev/nbdN <active layer>` then mounting the partition works, but the process with `--fork` lives only as long as the command that started it: the session cgroup takes it down, the next command meets "I/O error, dev nbdN, sector 0" in `dmesg` and "bad superblock" from mount, and the partition nodes /dev/nbdNpM stay stale between sessions. The mount option `nologreplay` is not supported by the kernel of this host: `btrfs: Unknown parameter 'nologreplay'`.

The stable route has no daemon: `qemu-img convert -O raw -S 4k <active layer> <raw file>`, then `losetup --read-only -P --show <raw file>`, then mount the subvolume. The loop device and the mount live in the kernel, so they survive between commands; a stale node cannot appear. Measured on test002: 60 GiB of virtual size, 11 GiB of raw file.

The root of a machine of this project lies in the subvolume @: mount it with `-o ro,subvol=@`. The top level alone shows only the subvolumes @, @points and @swap. /home is NOT a subvolume of its own: it is an ordinary directory inside @ and travels with it, so no machine of this project may give it a subvolume. That is the layout every machine of the project is meant to carry; the paragraph "The root subvolume" below records the consequences of a root that lies on the top level, the recipe that moves such a root into @, and which machines of this record carry the layout already. Keep the whole sequence, from the conversion or the connect to the mount, in one command when using nbd.

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

Measure the result with care: df does not drop while a running process holds deleted files open (lsof +L1 names them), so read the freed size after the session restarts, and the section below names the tools that tell what the size of the machine is made of.

## The size of the image file

The file the machine is baked into is smaller than the files it carries, and reading that size wrongly is easy. `du` reports the logical size of the data, which on btrfs with compression is the size before compression (measured on the tree of test002 on 2026-10-14: 6.3 GiB of logical data occupy 2.9 GiB on disk, so the compression halves it), and `df` reports what the allocator of the filesystem holds rather than what the files use. `compsize -x <mount point>` is the tool that answers the real question, and the host does not carry it: `apt-get download btrfs-compsize`, then `dpkg-deb -x btrfs-compsize_*.deb <directory>` and run `<directory>/usr/sbin/compsize`. `btrfs qgroup show -reF <mount point>` adds what each subvolume refers to. For the image file itself, `qemu-nbd --read-only --connect=/dev/nbdN <image>` and then `qemu-img map --output=json <image>` list the clusters that are really allocated, and comparing their offsets with the partition table of the image (`lsblk -b -n -o NAME,START,SIZE /dev/nbdN`) says how much of each partition is allocated inside the file.

Measured on pyntara_server_image on 2026-10-14: the file held 7.247 GiB, divided as EFI 0, bios_grub 0, /boot 0.366 GiB and the encrypted root 6.880 GiB, which sums to the file, so no gigabyte is missing at that level. Inside the encrypted root, read through `cryptsetup open --readonly --key-file <passphrase file>` and a read-only mount, the live file data measured 2.9 GiB on disk against 6.3 GiB of logical data, the btrfs metadata about 0.4 GiB, and btrfs reported roughly 3.7 GiB used once the preallocated swap file was left out of the count. About 3.2 GiB inside that partition had therefore been written and then freed while the qcow2 kept it: the file was that much larger than the data it carried. The swap file was not the cause, and the map proved it: a written swap file of 8.13 GiB would appear as one contiguous region of about eight gigabytes, while the largest region of that file measured about 0.5 GiB, so the swap file really stays holes.

The cause is one attribute of the domain. The disk of that machine carried `<driver name='qemu' type='qcow2'/>` with no discard, so a block the guest freed stayed allocated in the image file. Both manuals name the rule. `btrfs(5)` on the mount options: discard is "useful for SSD/NVMe devices, thinly provisioned LUNs, or virtual machine images; however, every storage layer must support discard for it to work", which is exactly this case, and the asynchronous mode is the default since kernel 6.2 when the device supports it. `fstrim(8)`: "errors from filesystems that do not support the discard operation, read-only devices, autofs and read-only filesystems are silently ignored", so a trim inside a guest whose disk cannot discard neither shrinks the file nor reports anything.

The cure is a disk that passes the discards on, the attribute `discard='unmap'` in the driver element of the domain, and then a trim inside the guest once the work that frees the space has run; a machine whose disk was built without it can be brought down by trimming the file itself, for which libguestfs is the usual tool (its `virt-sparsify` did not start on this host, so that route is recorded from the tool's purpose and not from a run here). A distributed image is normally brought to the size of the data it carries that way, which for this machine means about three gigabytes plus /boot in place of 7.247 GiB. The limit of this record: the benches of the project carry the attribute and their trims ran, but the size of an image file before and after such a trim was never compared here, so the amount a trim gives back is read from the mechanism and not from a measurement of this bench.

Measured on pyntara_server_image2 on 2026-10-15, which answers the question the paragraph above left open and adds one step the paragraph above misses. The disk of that machine carried `discard='unmap'` and its root is a LUKS2 volume, and the trim inside the guest reached nothing: `fstrim -v /` answered "the discard operation is not supported" and `/sys/block/dm-0/queue/discard_max_bytes` read 0, while the virtual disk itself offered the operation (2147483136 bytes). A default LUKS mapping does not pass the discards on, so the attribute of the domain is not enough when the root is encrypted: the crypttab entry of the root must carry the option (`cryptroot UUID=... /boot/pyntara.tmp luks,discard`) and the initramfs must be rebuilt, because the boot reads the crypttab from there and not from the machine. Measured after that change and a reboot: `cryptsetup status cryptroot` reports `flags: discards`, the mapping reads the same 2147483136, and `fstrim -v /` released 87 GiB of the free space of the filesystem, which took the file from 6.7 GiB on disk to 4.5 GiB. The swap file of that machine had been written to during its provisioning run, and removing it after the last boot released a further 2.5 GiB, so a swap file that has carried data occupies real space inside the file, unlike the preallocated one of the paragraph above, which had never been written and stayed holes.

Compressing an image whose root is encrypted buys almost nothing, and the measurement is worth recording so the work is not repeated: converting that image with `-c` and `compression_type=zstd` took 1 minute 39 seconds against 38 seconds for the plain conversion and gained 28 MiB, 0.7 per cent, because the data sits inside LUKS and is already incompressible. A plain `qemu-img convert -O qcow2` writes the clusters that carry data, leaves the zeros out and reads back without errors; measured on the image of 2026-10-15: 100 GiB of virtual size, 3.9 GiB of disk size, 3.9 per cent of the clusters allocated. The artifact to keep is therefore the plain sparse qcow2.

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

The autoinstall has to sit at /autoinstall.yaml, one of the discovery paths of subiquity. It names the source id, marks no section interactive (`interactive-sections: []`), and describes the storage: GPT, a 1 MiB bios_grub partition (curtin skips it on UEFI and creates it for BIOS), a 1 GiB EFI partition, a 1 GiB ext4 partition mounted at /boot, and the rest of the disk as one LUKS2 partition that holds the btrfs root of the machine, mounted with `compress=zstd:15,noatime`, written into the fstab of the target. The line carries no autodefrag, because a continuous defragmentation breaks the extents a save point shares with the root and the storage task strips that option from every machine it meets, and it names no subvolume of its own, because no action of the installer can create one; the paragraph "The root subvolume" below records how the late commands of a seed give it to a machine this image installs, and how a machine whose root lies on the top level is changed afterwards. It carries no `shutdown` key, and it must not: the schema of this subiquity allows only `reboot` or `poweroff`, the default is `reboot`, and a `poweroff` (which the first builds carried) leaves the user with a dark screen and no result, which is wrong for a machine whose whole point is a working system. Its keyboard section names the layout explicitly: without it the keyboard step of subiquity runs `setupcon --save-only` with whatever model the live session detected, and a model that comes out empty makes setupcon exit 1 without a message, which aborts the installation after curtin has already finished.

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

Read the machine read-only and diff it against the tree. `guestmount` reaches a stopped domain in one step: `guestmount -a <active layer> -m /dev/sda2:/:subvolid=5 --ro <dir>`, where the mount option belongs to the `-m` specification, because `-o` hands its options to FUSE and rejects `subvolid=5`, and `-i` refuses this disk with "multi-boot operating systems are not supported" (measured 2026-10-09). The top level shows @, @points and @swap at once, so the root subvolume is readable through one mount, and /home comes with it because it lies inside @; the raw file plus loop route above is faster and stays the way for a full export.

Ask the machine what changed instead of copying it: `find <mount>/@ -xdev -newermt '<export time>' -type f`, which already covers /home because it lies inside @, with the same exclusions as the export, and diff the configuration directories against the tree (`diff -rq` over `home/i/.config`, `home/i/.local/share`, `etc/xdg`). Copy only the files the diff names, then rebuild the squashfs and the ISO as below.

Measured example, 2026-10-09: a machine installed from an earlier image met the screen sharing question of the KDE portal on every new remote session, and the answer to it is a pair of files rather than one. The portal record lives in `home/i/.local/share/flatpak/db/screencast` and is keyed by the session token, and the client keeps that same token in `home/i/.config/rustdesk/RustDesk_local.toml` as `wayland-restore-token`. Both files were taken from a machine where the dialog had been answered, and the connection to the rebuilt image then started with no dialog (docs/spec/rustdesk-setup.md, Screen sharing without a dialog). A store record whose token no client presents is worth nothing: the first attempt carried the store file alone, with a token the client of that machine did not hold, and the dialog stayed.

## Rebuild

xorriso -indev ISO -outdev OUT -boot_image any replay -map filesystem.squashfs /casper/filesystem.squashfs -map initrd /casper/initrd -map md5sum.txt /md5sum.txt -commit keeps the BIOS El Torito entry, the hidden EFI image and the GPT.

Update both changed lines of /md5sum.txt. xorriso extracts that file read-only and root-owned, so write the new one through tee.

zstd at -Xcompression-level 19

Build the squashfs as root: run by an ordinary user, mksquashfs cannot read /var/lib/snapd and silently leaves it out of the image (measured: 1.7 MiB instead of 2.45 GiB). Write it on a normal filesystem and not in /tmp, which is a tmpfs of 7.7 GiB on this host and makes mksquashfs die with a fatal error before the end. Check the exit status of mksquashfs and read the log instead of piping it away: a piped run hides both the error and the status, which is how a truncated image was built twice. Measured on 2026-10-08: a tree of 12 GiB gave 6.7 GiB of squashfs at zstd level 1 and an ISO of 7.4 GiB. Measured on 2026-10-09 on the slimmed tree: 6.0 GiB of tree gave 2.58 GiB of squashfs, 44.7 per cent of 5.9 GiB uncompressed, with 172479 inodes and 122436 files, and the run took about two minutes on eight processors at the default zstd level.

## Verify

Boot in QEMU. The key exported with the tree logs into the live session, which then names its host, user, groups, sessions and services; only that shows the session equal to the machine.

Checks that have caught real defects: the size and the file count of the squashfs against the tree, the boot record of the rebuilt ISO (El Torito, the hidden EFI image and the GPT), the md5sum lines inside the ISO, the payload in /usr/local/share/pyntara-installer of the live session, and then a full installation in a virtual machine: the fstab of the target, the mount options of its root, the files of the ESP, and a boot of the installed system with its services active. Mount the produced squashfs read-only and look inside before it becomes an ISO: /usr/bin/snap, the four files of /usr/local/share/pyntara-installer, the wallpapers the Plasma configuration names, and /proc, /sys and /dev as empty directories. A tree that lost /var/lib/snapd and a tree that lost /proc both looked complete until the image was mounted and its directories were counted.

## Server image from the same recipe

The desktop image is a live USB that also installs to disk. A server image that is written
straight onto a disk needs none of that: it carries the same storage layout and the same
provisioning on a plain Ubuntu Server base without the desktop packages, and it is produced
without the casper tree, the squashfs and the installer payload. Walked on 2026-10-14 with
Ubuntu Server 26.04.1.

The source is the server ISO of the same release the desktop base uses,
ubuntu-26.04.1-live-server-amd64.iso. Its installer and curtin are the same programs the
desktop image carries, so the storage schema of the autoinstall behaves identically.

The machine is created with the shape of the target: firmware BIOS, the i440fx chipset, a
random number generator device, a disk of the target size and the default bridge. The disk size
equals the size of the target disk, so the produced image needs no partition table fix after it
is written.

virt-install --connect qemu:///system --name NAME --memory 3072 --vcpus 12 --cpu host-model
--disk path=DISK.qcow2,format=qcow2,bus=virtio --cdrom SERVER.iso
--disk path=SEED.iso,device=cdrom --network bridge=virbr0,model=virtio
--graphics spice,listen=127.0.0.1 --video virtio --rng /dev/urandom --noautoconsole

Three traps of this path, all met on 2026-10-14. The hypervisor runs as the user libvirt-qemu
and cannot traverse a home directory, so media kept there are unreadable by the guest and
virt-install says so; move the media into the images directory instead of widening the
permissions of the home directory. The medium needs a boot order, otherwise the firmware starts
the hard disk and reports "Boot failed: not a bootable disk". The medium also needs its file in
the persistent definition: the running instance may hold it while the saved definition does
not, which `virsh domblklist` shows as an empty Source, and the firmware then reports
"Boot failed: Could not read from CDROM (code 0003)"; supply it with
`virsh change-media DOMAIN hda ISO --insert --config`.

The installer reads an autoinstall from a cloud-init NoCloud data source, so a small ISO with
the label CIDATA carrying user-data and meta-data is enough and the server ISO needs no
rebuild. Build the seed with

```sh
genisoimage -output seed.iso -volid CIDATA -joliet -rock user-data meta-data
```

The user-data is a cloud-config whose top-level key is `autoinstall`: the identity (hostname,
the human account, its password hash and the authorized key), the `ssh` section that installs
the openssh server, the storage section and the late commands.

The installer does not apply the autoinstall on its own: it stops at the question "Continue
with autoinstall? (yes|no)" on its own console and proceeds only after the answer yes is typed
there. The desktop image answers that question by piping yes into the installer client; a run
driven from the console answers it at the console, and it is the single interaction of this
path. The installer names the way to remove the question: an `autoinstall` word on the kernel
command line makes it start without asking. Until that word is added, a console run must answer
the question, and a run that only watches the screen waits forever; answer it with the console
input, for example `virsh send-key DOMAIN --codeset linux KEY_Y KEY_E KEY_S KEY_ENTER`.

The storage layout is the one of the desktop autoinstall with the boot partition reduced for
space: a 1 MiB bios_grub partition, a 1 GiB ESP, a 512 MiB ext4 /boot, and the rest of the disk
as one LUKS2 partition holding the btrfs root of the machine, mounted with
compress=zstd:15,noatime.
The storage section carries swap size zero for the reason the desktop autoinstall does. The
separate unencrypted /boot is what makes the automatic unlock possible, because the boot loader
reads the kernel and its initial ramdisk before anything is decrypted.

The automatic unlock is the same as the desktop image and is added by the late commands: copy
the passphrase onto /boot/pyntara.tmp, point the crypttab entry at it, add
KEYFILE_PATTERN=/boot/pyntara.tmp to /etc/cryptsetup-initramfs/conf-hook, and rebuild the
initramfs with update-initramfs -u -k all. Write the key file from a base64 form of the
passphrase, so no quoting of the passphrase can break the command. Build the user-data with a
script that reads the passphrase file, so the value never reaches a command line.

A trap of the boot loader. This installer boots in BIOS mode, so curtin installs the i386-pc
boot loader; and when the ESP carries `grub_device: true` curtin installs it INTO that
partition. A GRUB core image cannot be embedded in a FAT file system, and on a GPT table the
blocklist fallback is refused, so the install dies with
`grub-install: error: will not proceed with blocklists` and the whole curthooks stage fails with
exit code 3. Everything after that stage, the late commands included, then never runs, so the
machine is left fully written but without a boot loader and without its automatic unlock. Mark
only the disk as `grub_device` and leave the ESP without it: curtin then installs the BIOS boot
loader onto the disk and places its core image in the bios_grub partition. The desktop image
does not meet this trap because its machine boots in EFI mode, where curtin installs x86_64-efi
into the ESP and never attempts the i386-pc install. Verified 2026-10-14.

A package the server base lacks. The automatic unlock writes into
/etc/cryptsetup-initramfs/conf-hook and relies on the initramfs hook that reads it,
/usr/share/initramfs-tools/hooks/cryptroot. A plain Ubuntu Server install from this ISO does not
carry those files, so a late command that writes into that directory fails and the machine is
left without the entry that names its key file; add `cryptsetup-initramfs` to the packages of
the autoinstall and the file and the hook appear. Verified 2026-10-14 on an install that
reached the end: the package is installed, the conf-hook file carries the KEYFILE_PATTERN line,
and /etc/crypttab names the key file.

The initramfs generator decides how the key gets inside. The hook and the KEYFILE_PATTERN
setting above belong to initramfs-tools, and a system whose initramfs is built by dracut ignores
them: the image then carries its /etc/crypttab but not the key file itself, and the machine asks
for the passphrase at boot even though every file on disk is right. The server base of this
release builds its initramfs with dracut (verified 2026-10-14: the initrd contains
usr/lib/dracut-lib.sh and an etc/crypttab, and no /boot/pyntara.tmp). dracut takes a file into
the image through its own configuration, so the late commands also write
/etc/dracut.conf.d/90-pyntara-unlock.conf with the line install_items+=" /boot/pyntara.tmp ",
and the initramfs is rebuilt afterwards.

The security updates of the installer. After the system is written, the installer runs its own
step postinstall/run_unattended_upgrades, which downloads and installs the security updates from
the network. It prints one line and then works in silence for minutes (observed 2026-10-14: the
disk kept writing long after the step named itself), it changes the very configuration the image
exists to carry, and it runs only when the installer sees a network, so two machines can produce
two different systems. The desktop build turns the step off with the apt configuration file the
desktop section above describes, whose package blacklist holds regular expressions (`.*`, never
`*`), and removes that file from the target in a late command. This path can carry the same file
and the same late command when the speed and the exact configuration matter more than the
updates themselves; the image then ships without them, and a machine that wants them takes them
in its first ordinary update.

Reading a failure. A failed install leaves the live environment at an error prompt and writes
the report /var/crash/TIMESTAMP.install_fail.crash inside the live environment, which is a
memory file system; the report carries the whole curtin and subiquity log with the Python
traceback of the failing step, so it is the thing to read. The live installer runs an SSH
server, but this path gives it no key and no password, so the live environment cannot be reached
over SSH and the report has to be brought out another way, for example by archiving it onto the
unencrypted /boot of the installed target and reading that disk from the host. The state the
failure left on the target is read the same way: `qemu-img convert --force-share` of the active
layer (a running domain holds a write lock that a plain read meets as "Failed to get shared
write lock"), then `losetup --read-only --partscan`, then `cryptsetup open --readonly
--key-file`, then a read-only mount. The files the late commands write are what tells whether
that stage ran: /etc/crypttab still holding `none`, a missing /etc/cryptsetup-initramfs and a
missing /boot/pyntara.tmp all say it did not run.

The root subvolume. The practice every btrfs installation of the distribution follows, and the
one this image is meant to carry, keeps the top level of the filesystem almost empty and holds
the data of the system in a subvolume of its own, mounted as / with `subvol=@`: the fstab of the
machine names the subvolume and the boot loader receives `rootflags=subvol=@`, the top level
carries nothing but the subvolumes, and a snapshot of the root can be mounted in its own right,
renamed into the place of the root, or sent away with `btrfs send`. An installation whose
autoinstall names no subvolume leaves the root on the top level of the btrfs filesystem, and
`btrfs subvolume list` then names none on the target; that was the state of the first server
image (2026-10-14), and the recipe below is what removes it. The server image built on
2026-10-15 carries the layout: its root is /dev/mapper/cryptroot[/@], and the top level carries
nothing but @, @points and @swap.

The consequences were measured on 2026-10-14 on an isolated btrfs image whose root is the top
level. `btrfs subvolume show /` answers `Name: <FS_TREE>`, so the root is not a subvolume at all.
`btrfs subvolume delete /` is refused with exit code 1, and the top level can never be read-only,
so the recovery that renames the root aside and puts a snapshot in its place cannot be performed
on such a machine, and `btrfs send` of the root is refused for the same reason. A read-only
snapshot of a mounted top level does succeed with exit code 0 and is listed, so the layout
difference does not by itself stop the points section, whose boot entries name the snapshot
directly. The end to end answer comes from the provisioning run on this image.

The change that removes the defect creates the subvolume on the fresh filesystem and mounts the
root with `subvol=@`. The file of the autoinstall cannot express it, and neither can an option
of the mount alone: the mount of a fresh btrfs with `subvol=@` fails with "No such file or
directory" while the same mount succeeds once the subvolume exists (both measured on 2026-10-14).
curtin offers no action that creates a subvolume, its storage actions being disk, partition,
format, mount, lvm_volgroup, lvm_partition, dm_crypt, raid, bcache, zpool, zfs, nvme_controller
and device, and subiquity adds only the choice of the disk and the sizes of the partitions. The
creation therefore has to come from the installer, either before curtin mounts the root or in a
late command after the system is in place, and the old files have to leave the top level
afterwards; that removal must skip the mount points of the other filesystems, because /boot and
/boot/efi are partitions of their own in this layout and removing their contents destroys the
kernels. Two details make the rest cheap once the subvolume exists: the options of a curtin
mount action reach both the mount of the installation and the fstab of the target, and the boot
parameter appears by itself, because /etc/grub.d/10_linux computes `rootflags=subvol=<the path of
the root relative to the root of the filesystem>` while the menu is rebuilt. Measured on a
machine whose root is a subvolume: /etc/default/grub names no such parameter, and grub.cfg
carries `rootflags=subvol=@`.

The change was made and measured on 2026-10-15. Two facts make it one operation. btrfs refuses
to move a subvolume, but it allows a plain directory to be renamed into one: measured on the
machine itself, `mv /var/tmp/probe/dir /var/tmp/probe/sub/` ended with exit code 0, and the moved
directory kept its file and stayed writable, so the content of the root is renamed into the new
subvolume instead of being copied. And the boot parameter needs no permanent setting of its own,
because the menu computes it as soon as the root is the subvolume.

The recipe, on the stopped machine, with the root filesystem mounted at its top level
(`mount -o subvolid=5 /dev/mapper/cryptroot /mnt/top`): `btrfs subvolume create /mnt/top/@`;
rename every entry of the top level into it, leaving out @ itself and every subvolume that is
already there, because a subvolume cannot be moved (on this machine the points section had
already created @points and @swap); write `subvol=@` into the options of the root line of
/@/etc/fstab; mount /dev/vda3 at /@/boot and add `rootflags=subvol=@` to every linux line of
/boot/grub/grub.cfg that carries no rootflags yet, which leaves the entries of the points
untouched because they name a rootflags of their own. The machine then boots from @. Measured
after that boot: the root is /dev/mapper/cryptroot[/@] with subvolid=260, the top level carries
only @, @points and @swap, no unit failed, the swap file stayed active on @swap and both points
stayed in @points. The setting in /etc/default/grub is not needed once the machine runs from the
subvolume, and was removed after that boot; `update-grub` still wrote `rootflags=subvol=@` into
the menu, which is the measurement the paragraph above had left to a later build.

For the server image the same change belongs to the installation and sits in the late commands
of its seed: they create @, move the content of the target into it, leaving out the mount points
of the other filesystems (/boot and /boot/efi are partitions of their own in that layout), write
`subvol=@` into the fstab and rebuild the menu. The move is the last command, because the
commands that reach the target through `curtin in-target` chroot into the top level and would
find an empty root once the content has moved. That route is written and not yet exercised: the
next build of this image is what proves it.

The installer reboots when it finishes, and with the medium first in the boot order the machine
starts the installer again; place the disk before the medium in the persistent boot order
(`virsh dumpxml --inactive`, raise the order of the medium, `virsh define`).

The installed machine is a plain Ubuntu Server and its provisioning is the ordinary run of the
project with the install mode set to server, as README.md and docs/spec/install-modes.md
describe.

## Operator access of the built server image

The autoinstall gives the human account a random password hash, so nobody knows the password of
the installed machine and its console cannot be used at all. The build of 2026-10-15 therefore
set a known password for the account i after the provisioning run and before the export, over
SSH as root: `printf 'i:%s\n' <value> | chpasswd`, with the single digit 1 as the value, so that
the operator logs in at the console once and changes it. Measured in the image: i is in the group
sudo (27), the console login with that password reaches a shell, and sudo accepts the same
password. This is not secrets/default.password: that file carries the password of default.vault,
so a run described as made with the default password names the vault source and says nothing
about the account.

The expiry policy of that password has to be the soft one, because the strict form locks the
machine out. `chage -d 0 i` marks the password expired and the machine then refuses a login by
key as well: measured, an SSH login as i with a key answers "Password change required but no TTY
available", so the account cannot be reached over the network and cannot be repaired without the
console of the provider. `chage -d $(date +%Y-%m-%d) -M 54750 -W 54750 i` instead keeps the
password valid for about 150 years with the warning window just as long, so the notice runs from
the first day and the login is never refused: measured, `chage -l i` answers "Password expires:
never", and both the console login and the SSH login as i print "Warning: your password will
expire in 54750 days." The password stays changeable with `passwd`, over SSH with a terminal
(`ssh -t`).

Root login by key. The seed appends the operator's public key to /root/.ssh/authorized_keys and
the provisioning adds keys of its own to that file. Measured in the image: the file holds two
lines, the operator's key (/home/i/.ssh/id_ed25519.pub) carrying the comment Pyntara_mesh and the
port forwarding key carrying the comment Pyntara_port_forwarding.

## Export and verification of the server image

The export is one conversion of the stopped machine, and three removals belong before it, all
measured on 2026-10-15.

The swap file. The machine of that build carried /swap/swapfile of 8.13 GiB inside the guest
filesystem. Removing it released 0.45 GiB of the image file and not the 8.13 GiB the file
occupies on the filesystem: the allocated clusters fell from 4.32 GiB to 3.87 GiB, because a swap
file nothing has been written into is a run of zeros and a qcow2 file allocates no cluster for a
run of zeros. The 2.5 GiB the paragraph above measured came from a file that had carried data.
The swap unit recreates the file at the first boot of the target machine, so the removal costs
the target nothing.

The resume address. Because the swap file existed when the boot menu was rebuilt, every kernel
line of the menu names it: measured in the image, resume=/dev/disk/by-id/dm-uuid-CRYPT-LUKS2-...
cryptroot with resume_offset=1058048. The image carries those words without the file they mean,
so a hibernation image would not be found; the swapfile task recomputes the address when the
provisioning runs on the target, and a resume that finds no image only prints a message and the
boot continues.

The network file of the build machine. Measured on this build the file was
/etc/netplan/00-build-dhcp.yaml, naming ens3 with dhcp4; it was removed before the export, so the
/etc/netplan of the artifact is an empty directory, and the consequence stands as the section
below describes: the machine written from this image comes up with no network of its own and
takes its address from the console of the provider.

The artifact. /var/lib/libvirt/images/pyntara-server-26.04.1-image.qcow2, 4152950784 bytes
(3.87 GiB) of a 100 GiB virtual disk, written by `qemu-img convert -O qcow2` from
pyntara_server_image2.qcow2; the artifact of the same morning, which lacked the operator access
of the section above, is kept beside it as
pyntara-server-26.04.1-image-before-operator-access.qcow2. Verified by reading the artifact
rather than the machine: `qemu-img check` reports no errors and 3.87 per cent of the clusters
allocated, `e2fsck -fn` on the /boot partition is clean, the top level of the btrfs filesystem
carries @, @points and @swap and nothing else, the fstab of the root names subvol=@, the crypttab
carries luks,discard, /boot/pyntara.tmp holds the 27 byte key file that unlocks the root, the
LUKS2 header is intact, the boot menu passes rootflags=subvol=@ on every kernel line,
/etc/machine-id is present, and both /swap and /etc/netplan are empty.

A trap of such a reading, met twice on 2026-10-15. The desktop session auto-mounts the partitions
of the nbd device through udisks under /run/media/i/<label>, and two consequences follow:
`cryptsetup close` answers "Device ... is still in use" and the mapping stays open, and a later
read of the same partition through a mount that survived the disconnect of the device answers
"Input/output error", which looks like a damaged filesystem while the file system check of that
very image was clean. Unmount those paths before closing the mapping, and read an "Input/output
error" of a second read as the stale mount until the read is repeated with no mount of the device
left in place.

## The network configuration of the build machine

The installer writes the network configuration of the machine it runs on, and that machine is the
build machine, not the target. Measured on 2026-10-15 on the server image of this recipe: the
target carried /etc/netplan/00-installer-config.yaml naming the interface of the build machine
(ens3), matching that card by its hardware address (52:54:00:7e:d8:83) and renaming the card to
the same name. Written onto another machine, whose card carries a different name and a different
address, that file matches no card at all: `netplan apply` answers "Cannot find unique matching
interface for ens18", the machine is left with no address and no network, and the defect is
visible only on its console, because nothing in the machine can be reached from outside any more.

So remove that file from the image before the image is written onto another machine, or at least
its match and set-name keys and its addresses, and let the provisioning run configure the network
of the machine the image lands on. A netplan file left behind is worse than no file at all: with
no file the interface keeps what the target does by default, while a file that matches nothing
leaves the machine with no address whatever.

The address of the target rarely comes from DHCP, and on this provider it does not come at all.
Measured on the Contabo machine of 2026-10-15: its segment carries no DHCP server, shown three
ways, by dhcpcd sending DISCOVER and receiving no answer and falling back to a link-local
address, by tcpdump catching one outgoing request and no reply in thirty seconds, and by
systemd-networkd standing in "degraded (configuring)" with an empty /run/systemd/netif/leases.
The address, the netmask and the gateway of such a machine are read from the provider and written
into the netplan file by hand; there they were 173.249.15.40/24 with the gateway 173.249.15.1.

Leave the resolver of the image alone. A machine built by this project answers names through
/etc/systemd/resolved.conf.d/pyntara-dnsproxy.conf and the dnsproxy service, as the machine of
that measurement does, so its netplan file carries no nameservers, and none should be added: a
nameserver written into the netplan file takes the resolution away from the chain the project
installs.

A machine whose network never comes up is still reachable through the console of the provider,
and that console accepts only characters that need no shift key: the digits, the lowercase
letters and the plain symbols. Every shifted symbol, the colon, the asterisk, the pipe and the
redirection sign among them, reaches the machine as a different character, which ruins a command
that carries it without saying so.

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
      options: compress=zstd:15,noatime
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

The option line of the root mount carries no autodefrag since 2026-10-14: the storage task strips that option from every machine it meets, because a defragmentation that runs continuously breaks the extents a save point shares with the root, and an installer that writes what a task then removes does the same work twice. The build tree file /home/i/Downloads/pyntara-iso/installer-payload/autoinstall.yaml.in still carries the earlier line and loses the option with the same change. The root of the system still lies on the top level of the filesystem, because neither curtin nor subiquity can create a btrfs subvolume from this file and a mount option alone cannot either: mounting a fresh btrfs with subvol=@ fails with "No such file or directory" (measured on an isolated image on 2026-10-14), while the same mount succeeds once the subvolume exists. The layout every machine of the project is meant to carry, the late commands that create it during the installation, and the recipe that gives it to a machine whose root lies on the top level, are recorded in the paragraph "The root subvolume".

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

## Writing an image onto the disk of a machine that already runs it

A machine that is already running can be reprovisioned with a prepared image over the
network, without the console of the provider, if the running system is first moved into
memory and the disk is left alone afterwards. Walked on 2026-10-16 from a Kubuntu host onto
a Contabo VDS (169.58.1.142, disk /dev/sda of 100 GiB, 7.9 GiB of memory, interface ens18).

Three things fail the write if they are not handled.

The running system dies during the write because the stream of written data evicts the
file-backed pages of its processes; a page that is needed again after that has to be read
from the disk that is being overwritten, and no new process can be started. The cure is to
move the whole running root into a memory file system and switch the root onto that copy, so
every command of the operation runs from memory.

The kernel writes to the disk on its own even when no program does: the periodic transaction
commit of btrfs, the on-line discard of a btrfs mounted with discard, and a swap file that
carries pages. The cure is to disable swap and freeze the old filesystem before the write.

The machine comes back with no network unless the image itself carries the address of the
target, because the image is built with an empty /etc/netplan and with cloud-init disabled,
so nothing configures the card.

Prepare the payload on the builder. Write the target's netplan into a copy of the image,
convert the copy to a raw file and compress it:

```
sudo cp --reflink=auto IMAGE.qcow2 IMAGE-target.qcow2
# open the copy read-write over nbd and LUKS, write /etc/netplan/01-target-network.yaml
# with mode 0600, check it with netplan generate --root-dir, then close the copy
qemu-img convert -O raw IMAGE-target.qcow2 /var/tmp/image.raw
zstd -T0 -3 -o /var/tmp/image.zst /var/tmp/image.raw
```

The archive is about the size of the allocated part of the image, because the data inside
LUKS does not compress and the empty clusters cost nothing.

Move the target's root into memory and switch onto it. The script that performs the switch
must place the next script inside the copy, and the root must be made private first:

```
mkdir -p /ram/root
mount -t tmpfs -o size=6500M,mode=0755 tmpfs /ram/root
cp /ram/stage2.sh /ram/root/stage2.sh
for d in bin sbin lib lib64 usr etc var root home opt srv media mnt; do
  cp -a --one-file-system "/$d" /ram/root/
done
mkdir -p /ram/root/dev /ram/root/proc /ram/root/sys /ram/root/run /ram/root/oldroot
mount --bind /dev /ram/root/dev
mount --bind /proc /ram/root/proc
mount --bind /sys /ram/root/sys
mount --bind /run /ram/root/run
swapoff -a
mount --make-rprivate /
cd /ram/root
pivot_root . oldroot
```

pivot_root fails with exit code 1 and no message while the root mount is shared, which is how
systemd leaves it, and `mount --make-rprivate /` is the step that makes the switch succeed.
The script that runs after the switch is read from the new root, so it has to be inside the
copy before the switch; one left beside the temporary file system is unreachable afterwards.
The copied root also has to carry the tool that writes and the one that decompresses
(dd and zstd here, both from the copied /usr).

Freeze the old filesystem and prove that the disk is quiet:

```
fsfreeze -f /oldroot
cat /sys/block/sda/stat
sleep 6
cat /sys/block/sda/stat
```

Two equal readings of the counters say that nothing writes. Remounting to read-only is not
the way here: with the files of a running system open the kernel answers "mount point is
busy".

Receive the image and write it. On the target:

```
busybox nc -l -p 9999 | zstd -dc | dd of=/dev/sda bs=8M conv=sparse,fsync status=progress
```

and on the builder:

```
cat /var/tmp/image.zst | nc -N TARGET_ADDRESS 9999
```

conv=sparse keeps the empty clusters of the raw image from being written, so the disk sees
only the allocated part. The listener must be reachable from the builder; a short test with a
temporary listener on the chosen port before the destructive phase costs a minute and removes
the doubt.

Reboot without the provider console:

```
sync
echo b > /proc/sysrq-trigger
```

The kernel performs the reset, so no process has to be started for it. The machine then
starts from the written image and, if the image carries the target's address, comes up on the
network by itself. On the run above the write over the network took about seventeen minutes
(3.84 GiB of archive over a link of about 6.5 MiB/s while the target decompressed 100 GiB) and
the machine answered on its key port within the next minutes.

Traps met on this route.

netplan set-name did not rename the interface. On the tested machine the kernel named the
card ens3 (on the VDS ens18) and the generated .link did not match, so the generated
.network, which carries the interface name only through the set-name key, matched nothing and
no address was configured at all. A netplan file that matches on the MAC address alone, with
no set-name, generates a .network with PermanentMACAddress and no name, and that applies
whatever the interface is called. This is the form to use for an image written onto an
unknown machine.

dracut leaves a DHCP configuration in the real root. The network module of the initramfs
writes /run/systemd/network/zzzz-dracut-default.network, which enables DHCP for every
non-loopback ethernet; it is present in the image's initrd as
usr/lib/dracut/dracut-default.network. On a segment without a DHCP server the initramfs
attempt only delays the boot. In the real root systemd-networkd applied the netplan file,
which won over it, but the order of the two is not a thing to rely on.

qemu-img convert wrote nothing to its standard output on the tested host: `qemu-img convert
-O raw IMAGE -` returned 0 and produced no byte, while the same command with a file name
works. Build the raw file first and compress it afterwards.

The size of the memory copy is the logical size of the tree, not the size of the image file.
The btrfs of the image is compressed, so the file holds about 3.9 GiB while the tree it
carries is about 6 GiB, and a memory file system stores the files uncompressed. The memory of
the target therefore has to hold the logical size, which is why the payload is sent over the
network instead of being stored in memory beside the copy; holding both at once does not fit
in 7.9 GiB.

The reboot is not clean. After the reset the machine showed a few failed units (atop,
fail2ban, tor) because the services were cut by the reset rather than shut down; the machine
was otherwise healthy, reachable, and resolved names through its own resolver chain.
