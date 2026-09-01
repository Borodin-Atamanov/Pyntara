# ImageMagick install

There is a dedicated ImageMagick installation task: imagemagick_setup.

The task installs ImageMagick from the Ubuntu archive with apt and makes it available on the command line. The version is not chased and not verified: the archive is the maintained source and receives its updates through the regular apt upgrade.

## Source and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos, which is a hard dependency of the task). On Kubuntu 26.04 and newer the archive already ships ImageMagick 7: the meta package imagemagick pulls imagemagick-7.q16, which owns the magick, convert and identify binaries through update-alternatives. The package recommends ghostscript, netpbm and libmagickcore-7.q16-10-extra, so PDF support and the extra coders arrive with the default apt install.

No third-party source is used. ImageMagick publishes no apt repository and only an AppImage as a prebuilt Linux binary; the AppImage carries an open security policy, is not integrated with apt and its bundled libraries conflict with the system ghostscript, so it is a worse fit for a provisioned machine than the archive package. The archive build has a fuller delegate set than the AppImage build. This is a deliberate simplification: the task installs whatever ImageMagick the archive carries, without a version gate.

## Installation

The task is idempotent. Every configured package that dpkg-query reports as installed is left alone; when none is missing the task reports already installed and changes nothing. Otherwise it refreshes the apt index once unless skip_apt_update, then installs each missing package individually through the shared install_packages helper (utils.py), with one initial attempt plus the configured retries. A package that still fails to install is an error TaskResult: the runner continues with the remaining tasks and never stops here. The report lists the installed packages and any apt index warnings.

## Security policy

After the packages are in place the task writes the tuned ImageMagick security policy over the system file cfg.policy_path (by default /etc/ImageMagick-7/policy.xml). The package original is saved once next to it as policy_path.bak; ImageMagick loads only the file named policy.xml, so the backup is never picked up. The policy step is idempotent too: when the target already matches the template nothing is written and an existing backup is never overwritten.

The template lives in task_data/imagemagick_setup/policy.xml in the repository clone. It keeps the security entries of the default open policy (indirect reads and the URL/HTTPS/HTTP delegates stay restricted) and raises the resource limits so processing uses all physical RAM, spills to disk when needed, waits at most a day and accepts very large images. ImageMagick clamps memory to the machine RAM and threads to the core count, so the configured 128GiB memory, 128 threads and the large size limits are safe ceilings, not wasted headroom.

## Parameters

All parameters live in the [imagemagick_setup] table of the config/ directory.

packages - the package names to install, the meta package imagemagick first
policy_path - the system policy file the tuned policy is written over
package_status_timeout_seconds - seconds the dpkg status query may take
package_install_retries - retry attempts after a failed package install
