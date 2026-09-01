# ffmpeg install

There is a dedicated ffmpeg installation task: ffmpeg_setup.

The task installs ffmpeg from the Ubuntu archive with apt and makes it available on the command line. The version is not chased and not verified: the archive is the maintained source and receives its updates through the regular apt upgrade. Installed means success.

## Source and version policy

The package comes from the Ubuntu archive (universe, enabled by add_extra_repos, which is a hard dependency of the task). On Kubuntu 26.04 and newer the archive carries a complete GPL ffmpeg build: the meta package ffmpeg pulls the ffmpeg, ffprobe and ffplay binaries together with the shared libav libraries, and the default build includes the major encoders, decoders, filters and hardware acceleration. The task also installs the build toolchain of the wayrecord capture engine: gcc, the Wayland and PipeWire development headers (libwayland-dev, libpipewire-0.3-dev) and pkgconf.

No third-party repository and no source build of ffmpeg are used. The archive build is the maintained source and needs no version gate; building from source would add only a newer version and niche nonfree components at the cost of manual rebuilds and shadowing the apt binary.

## Installation

The task is idempotent. Every configured package that dpkg-query reports as installed is left alone; when none is missing the task reports already installed and changes nothing. Otherwise it refreshes the apt index once unless skip_apt_update, then installs each missing package individually through the shared install_packages helper (utils.py), with one initial attempt plus the configured retries. A package that still fails to install is an error TaskResult: the runner continues with the remaining tasks and never stops here. The report lists the installed packages and any apt index warnings.

## Wayland screen recording bridge

The task builds the wayrecord capture engine: the C source task_data/ffmpeg_setup/wayrecord.c is compiled with gcc against libwayland-client and libpipewire to the configured system path (pyntara-wayrecord), and a desktop entry is written to the configured wayrecord_desktop_path that lists X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1. The engine is therefore a trusted application: KWin grants it the direct screencast protocol exactly like Spectacle, so recording needs no portal and never shows a screen dialog. The build and the desktop deploy are idempotent: an engine or entry that already matches the built artifact is left alone.

pyntara-wayrecord is a capture source: the ffmpeg CLI cannot capture Wayland natively, so the engine records the screen through KWin's direct screencast protocol, links a PipeWire consumer stream to the captured node and writes raw frames to stdout. The caller pipes the stream into ffmpeg and controls every encoding parameter, for example:

pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4

The stream is always BGRA at the native screen size, because the KWin screencast node delivers only the four-byte-pixel formats. The frame rate is controlled with --caps (a GStreamer-style caps string, default video/x-raw,format=BGRA,framerate=30/1): the engine drops frames to the requested rate, so slow-motion and timelapse work (for example framerate=1/2 emits one frame every two seconds). Format and size conversions happen in ffmpeg, not in the capture: requesting a format the node cannot produce prints a hint with the conversion filter (for example -vf format=nv12 for hardware encoding), and a size that differs from the screen prints a scale hint. The resolved stream is reported to stderr so the caller can match -pix_fmt, -s and -r; stdout carries only raw frames. The capture stops when the pipe closes (ffmpeg exits) or on Ctrl+C; --help shows examples.

The old portal-based script task_data/ffmpeg_setup/wayrecord.py stays in the repository as a manual fallback for systems without the trusted-app grant; the task itself does not deploy it.

## Parameters

All parameters live in the [ffmpeg_setup] table of the config/ directory.

packages - the package names to install, the meta package ffmpeg first, then the engine build toolchain
wayrecord_bin_path - the system path the wayrecord capture engine is built to
wayrecord_desktop_path - the desktop entry that grants the screencast interface to the engine
package_status_timeout_seconds - seconds the dpkg status query may take
package_install_retries - retry attempts after a failed package install
