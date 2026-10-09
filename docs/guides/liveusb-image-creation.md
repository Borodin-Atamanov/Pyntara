# Live USB image creation

Baking a Pyntara-configured machine into a Ventoy live USB image that also offers installation to disk. Walked on 2026-10-07 on Kubuntu 26.04.1.

## Source

The desktop ISO of the same release: kubuntu-26.04.1-desktop-amd64.iso, hybrid BIOS plus EFI plus GPT, carrying /casper/filesystem.squashfs, /casper/initrd and /casper/vmlinuz.

## Export

Copy the running machine with rsync as root. Give no -x: /home, /points and /swap are separate btrfs subvolumes and -x skips them silently.

Exclude /proc /sys /dev /run /tmp /mnt /media /cdrom /lost+found /snap /boot/efi /swap/swapfile /var/cache/apt /var/lib/apt/lists /var/lib/snapd/cache /var/log/journal /var/tmp and /points. /points holds the save point and the work copy of btrfs_points_setup, 26 GiB of duplicates on the test machine.

Deleting the snap seed frees nothing: its files are hardlinked into /var/lib/snapd/snaps.

## Slimming before export

The Export list alone frees little; the packages do. Measured on test002 (Kubuntu 26.04.1, btrfs compress=zstd:15, 3057 packages, 16 GiB used): /swap/swapfile held 8.5 GiB and /var/cache/apt 2.0 GiB, and removing those plus locales except ru and en, /usr/share/doc, wallpapers, libreoffice, texlive, pandoc, calibre, node, java, the build tools, the caches and the logs took the machine to 7.5 GiB. What remains is what makes it work: firmware 735 MiB, kernel modules, the core libraries, the KDE stack, Firefox.

Never purge a package a kept one depends on. Measured traps on Kubuntu: kf6-breeze-icon-theme and fonts-noto-core take plasma-workspace, plasma-desktop and kubuntu-desktop with them; cpp-15 does the same through x11-xserver-utils and cpp; libllvm21 takes mesa-vulkan-drivers and mesa-libgallium, hence the graphics. Simulate every removal first with apt-get -s purge <packages> and read the Purg list for plasma, kde, kwin, sddm, kubuntu, mesa, llvm.

Snapshot before a removal so the step is reversible: virsh snapshot-create-as <domain> <name> --disk-only --atomic, revert with virsh snapshot-revert <domain> <name>.

Measure the result with care: compress=zstd makes du overstate the on-disk cost of a directory, and df does not drop while a running process holds deleted files open (lsof +L1 names them); read the freed size after the session restarts.

## Identities to keep

Don't blank /etc/machine-id. RustDesk decrypts enc_id and password in its RustDesk.toml with a key derived from the machine identity, so a fresh machine-id makes it report a new ID, lose the password and rewrite the file. The same holds for any state encrypted to the machine.

Keep /etc/ssh host keys, /var/lib/tor with its onion keys, /var/lib/i2pd, /var/lib/pyntara and /etc/pyntara/pass.

## Live user and host

casper reads /etc/casper.conf from the INITRAMFS, not from the squashfs, and copies its own copy over the live root at the end of the boot. With an empty FLAVOUR it overwrites USERNAME and HOST with the first word of /cdrom/.disk/info, which is kubuntu.

So set USERNAME, USERFULLNAME, HOST and a non-empty FLAVOUR in /etc/casper.conf inside /casper/initrd, and repack the initrd as its uncompressed microcode cpio plus a gzip-compressed main cpio. Autologin then follows on its own: casper appends an [Autologin] section to /etc/sddm.conf.

## Installer

A Pyntara machine has no installer; Calamares goes into the tree through chroot.

It creates the user with useradd and fails when the user exists, so keep the baked user in the image and delete it on the target before the users job with a shellprocess instance. Removing it from the image instead breaks the live session.

Taking a module out of the show list does not hide its page: a view module left in exec still gets one, as keyboard and users do. Remove it from exec too.

Calamares must run as root, which the desktop entry does through sudo. A run that stops leaves the target mounted under /tmp/calamares-root-*, unmount before the next run.

Open failure: Calamares 3.3.14-0ubuntu25.26.04.1 from the archive dies with SIGSEGV in the job thread on the first Python job of the second batch, localecfg, along PythonJob::exec, CalamaresPython::Helper::createCleanNamespace, boost::python, PyDict_New. The Python jobs of the first batch pass. The renderer is innocent, and the logged line "The X11 connection broke" only follows the death.

Text-mode alternative: curtin, whose sources accept squashfs:// and copy a squashfs image to the target, with subiquity as the interactive text installer above it.

## Rebuild

xorriso -indev ISO -outdev OUT -boot_image any replay -map filesystem.squashfs /casper/filesystem.squashfs -map initrd /casper/initrd -map md5sum.txt /md5sum.txt -commit keeps the BIOS El Torito entry, the hidden EFI image and the GPT.

Update both changed lines of /md5sum.txt. xorriso extracts that file read-only and root-owned, so write the new one through tee.

zstd at -Xcompression-level 19

## Verify

Boot in QEMU. The key exported with the tree logs into the live session, which then names its host, user, groups, sessions and services; only that shows the session equal to the machine.
