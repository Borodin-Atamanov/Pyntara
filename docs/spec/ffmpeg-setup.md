# ffmpeg install

There is a dedicated ffmpeg installation task: ffmpeg_setup.

The task installs ffmpeg from the Ubuntu archive with apt and makes it available on the command line. The version is not chased and not verified: the archive is the maintained source and receives its updates through the regular apt upgrade. Installed means success.

## Source and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos, which is a hard dependency of the task). On Kubuntu 26.04 and newer the archive carries a complete GPL ffmpeg build: the meta package ffmpeg pulls the ffmpeg, ffprobe and ffplay binaries together with the shared libav libraries, and the default build includes the major encoders, decoders, filters and hardware acceleration.

No third-party repository and no source build are used. The archive build is the maintained source and needs no version gate; building from source would add only a newer version and niche nonfree components at the cost of manual rebuilds and shadowing the apt binary.

## Installation

The task is idempotent. Every configured package that dpkg-query reports as installed is left alone; when none is missing the task reports already installed and changes nothing. Otherwise it refreshes the apt index once unless skip_apt_update, then installs each missing package individually through the shared install_packages helper (utils.py), with one initial attempt plus the configured retries. A package that still fails to install is an error TaskResult: the runner continues with the remaining tasks and never stops here. The report lists the installed packages and any apt index warnings.

## Parameters

All parameters live in the [ffmpeg_setup] table of the config/ directory.

packages - the package names to install, the meta package ffmpeg first
package_status_timeout_seconds - seconds the dpkg status query may take
package_install_retries - retry attempts after a failed package install
