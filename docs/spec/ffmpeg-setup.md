# ffmpeg install

There is a dedicated ffmpeg installation task: ffmpeg_setup.

The task installs ffmpeg from the Ubuntu archive with apt and makes it available on the command line. The version is not chased and not verified: the archive is the maintained source and receives its updates through the regular apt upgrade. Installed means success.

## Source and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos, which is a hard dependency of the task). On Kubuntu 26.04 and newer the archive carries a complete GPL ffmpeg build: the meta package ffmpeg pulls the ffmpeg, ffprobe and ffplay binaries together with the shared libav libraries, and the default build includes the major encoders, decoders, filters and hardware acceleration. The task also installs the runtime dependencies of the wayrecord bridge: python3-dbus (the portal client) and gstreamer1.0-pipewire (the stream capture element).

No third-party repository and no source build are used. The archive build is the maintained source and needs no version gate; building from source would add only a newer version and niche nonfree components at the cost of manual rebuilds and shadowing the apt binary.

## Installation

The task is idempotent. Every configured package that dpkg-query reports as installed is left alone; when none is missing the task reports already installed and changes nothing. Otherwise it refreshes the apt index once unless skip_apt_update, then installs each missing package individually through the shared install_packages helper (utils.py), with one initial attempt plus the configured retries. A package that still fails to install is an error TaskResult: the runner continues with the remaining tasks and never stops here. The report lists the installed packages and any apt index warnings.

## Wayland screen recording bridge

The task deploys the wayrecord bridge: the script task_data/ffmpeg_setup/wayrecord.py is copied to the configured system path (pyntara-wayrecord) and made executable. The deploy is idempotent: a target file that already matches the template is left alone.

pyntara-wayrecord records the Wayland screen to a file with ffmpeg. The ffmpeg CLI cannot capture Wayland natively, so the script asks the xdg-desktop-portal ScreenCast portal for a PipeWire stream (a file descriptor plus the stream node id), reads that stream with the GStreamer pipewiresrc element, and feeds the raw frames into ffmpeg, which encodes them with the chosen codec. On the first run the KDE portal shows the screen-choice dialog once; the script then saves the single-use restore token the portal returns and passes it back on later runs, so the recording starts without asking again. The token lives in the per-user file wayrecord_token under the pyntara config directory, or in the path of the PYNTARA_WAYRECORD_TOKEN environment variable. The output defaults to ~/Videos/wayrecord_TIMESTAMP.mp4; --fps, --codec (a vaapi encoder selects the VAAPI device path) and --seconds options tune the recording, and Ctrl+C stops it.

## Parameters

All parameters live in the [ffmpeg_setup] table of the config/ directory.

packages - the package names to install, the meta package ffmpeg first
wayrecord_bin_path - the system path the wayrecord recording script is deployed to
package_status_timeout_seconds - seconds the dpkg status query may take
package_install_retries - retry attempts after a failed package install
