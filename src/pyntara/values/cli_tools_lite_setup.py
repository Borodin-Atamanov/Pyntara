"""Values of the cli_tools_lite_setup task.

The everyday console utility set installed from the Ubuntu archive: shell
completion, file and storage tools, security, terminal multiplexers, file
managers, archivers, system information, resource monitors, network and disk
tools. The set is deliberately free of the media and document tools, which are
the list of cli_tools_heavy_setup: a media toolchain takes minutes and must not
hold back the tools the machine needs early in the run.

The task checks the real system state with dpkg-query and installs only what is
missing, so repeated runs change nothing. The apt index is refreshed once
before the first install unless the run skips it. The task succeeds when at
least PACKAGE_SUCCESS_THRESHOLD_PERCENT of the set is installed after the run:
a single failing package is not fatal by itself, and every package that could
not be installed is named as a warning of the completed task with its own
reason.
"""

from __future__ import annotations

# Minimum installed share of the package set, in percent, for the task to
# succeed. Below this share the task reports the shortfall; the run stays
# detectable as incomplete either way.
PACKAGE_SUCCESS_THRESHOLD_PERCENT: int = 70

# The everyday console utilities. A name here must be the real package,
# because dpkg-query cannot see a virtual name: a virtual name would look
# missing forever and be reinstalled on every run.
PACKAGES: tuple[str, ...] = (
    # shell completion
    "bash-completion",  # programmable completion for the bash shell
    "hstr",  # better history in console
    # file operations
    "tree",  # displays an indented directory tree, in color
    "rsync",  # fast, versatile, remote and local file-copying tool
    "trash-cli",
    # storage monitoring
    "lsof",  # list open files
    "smartmontools",  # control and monitor storage systems through S.M.A.R.T.
    # security and cryptography
    "gnupg",  # GNU privacy guard, a free PGP replacement
    "ca-certificates",  # common CA certificates for TLS verification
    "openssl",  # OpenSSL cryptographic toolkit
    "sshpass",  # non-interactive SSH password authentication
    # terminal multiplexers
    "tmux",  # terminal multiplexer with sessions and windows
    "screen",  # terminal multiplexer with VT100/ANSI terminal emulation
    # file managers
    "mc",  # Midnight Commander, two-panel console file manager
    "nnn",  # lightweight terminal file browser
    # archivers
    "unrar",  # extractor for the proprietary RAR archive format
    "unrar-free",  # free replacement for unrar
    "rar",  # creates RAR archives
    "unzip",  # ZIP archive extractor
    "7zip",  # modern 7-Zip archiver, replaces the removed p7zip-full
    "xz-utils",  # XZ and LZMA compression tools
    "zstd",  # Zstandard compression tool with high ratio and speed
    "lrzip",  # long-range ZIP, compression for large files
    "pigz",  # parallel gzip implementation using all CPU cores
    "lz4",  # extremely fast LZ4 compression tool
    # system information
    "inxi",  # full system information report
    "lsscsi",  # list SCSI and other storage devices
    "lshw",  # detailed hardware information
    # resource monitoring
    "htop",  # interactive process viewer
    "nmon",  # system performance monitor for CPU, memory, network and disk
    "ncdu",  # disk usage analyzer with an interactive curses interface
    # network tools
    "net-tools",  # classic tools: ifconfig, netstat, route, arp
    "nmap",  # network scanner
    "tcpdump",  # command-line packet analyzer
    "traceroute",  # trace the network path to a remote host
    "whois",  # client for the whois directory service
    "wget",  # non-interactive network downloader
    "netcat-openbsd",  # nc, arbitrary TCP and UDP connections
    "bind9-dnsutils",  # dig, nslookup and host; the real package behind dnsutils
    "nload",  # console network traffic monitor
    "speedometer",  # console bandwidth usage meter
    "bmon",  # bandwidth monitor with an interactive curses interface
    "iftop",  # real-time bandwidth usage by connection
    "nethogs",  # per-process network traffic monitor
    "iptraf-ng",  # interactive IP network traffic monitor
    # disk and file tools
    "exfat-fuse",  # read and write exFAT filesystems through FUSE
    "fdupes",  # find and remove duplicate files
    "lynx",  # text mode web browser, html to text
    # automation and configuration
    "expect",  # automate interactive terminal programs
    "augeas-tools",  # command-line tools for the Augeas configuration editor
    # small utilities
    "calc",  # arbitrary precision calculator
    "bc",  # arbitrary precision calculator language
    "perl",  # scripting language used in pipelines
    "libc-bin",  # iconv, text encoding conversion
    "hollywood",  # decorative fake Hollywood-style terminal activity
)

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error. The pair of package install values
# comes from the shared module common.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "PACKAGE_SUCCESS_THRESHOLD_PERCENT",
)
