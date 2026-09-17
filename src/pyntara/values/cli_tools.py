"""Values of the cli_tools task.

The console utility set installed from the Ubuntu archive. The task checks the
real system state with dpkg-query and installs only what is missing, so
repeated runs change nothing. The apt index is refreshed once before the first
install unless the run skips it. The task succeeds when at least
PACKAGE_SUCCESS_THRESHOLD_PERCENT of the set is installed after the run: a
single failing package is not fatal by itself, and every package that could not
be installed is named as a warning of the completed task with its own reason.
"""

from __future__ import annotations

# Minimum installed share of the package set, in percent, for the task to
# succeed. Below this share the task reports the shortfall; the run stays
# detectable as incomplete either way.
PACKAGE_SUCCESS_THRESHOLD_PERCENT: int = 70

# The console utilities. A name here must be the real package, because
# dpkg-query cannot see a virtual name: a virtual name would look missing
# forever and be reinstalled on every run.
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
    # media tools
    "mediainfo",  # technical details of media files, codecs and streams
    "libimage-exiftool-perl",  # exiftool; the real package behind the virtual name
    "mkvtoolnix",  # mkvmerge and mkvextract, MKV muxing and extraction
    "webp",  # cwebp and img2webp, WebP encoder and animation
    "libheif-examples",  # heif-enc and heif-convert, HEIC and HEIF encoding
    "eyed3",  # eyeD3, mp3 tag editor
    "jpegoptim",  # optimize the jpeg file size
    "pngquant",  # lossy PNG compressor with palette reduction and dithering
    "jhead",  # jpeg EXIF header tool with autorotate
    "libjpeg-turbo-progs",  # jpegtran, lossless jpeg operations
    "exiftran",  # lossless jpeg rotation by EXIF
    "tesseract-ocr",  # OCR engine
    "tesseract-ocr-eng",  # English language data for tesseract
    "tesseract-ocr-rus",  # Russian language data for tesseract
    "tesseract-ocr-spa",  # Spanish language data for tesseract
    "mat2",  # strip metadata from files
    # pdf and documents
    "poppler-utils",  # pdfunite, pdfseparate, pdfattach, pdfinfo, pdftotext, pdftoppm
    "texlive-extra-utils",  # pdfcrop, crop pdf margins
    "pdftk-java",  # pdftk, pdf manipulation and metadata
    "qpdf",  # pdf encryption, cleaning and normalization
    "ghostscript",  # gs, postscript and pdf interpreter
    "pandoc",  # document format converter
    "lynx",  # text mode web browser, html to text
    "calibre",  # ebook-convert, ebook format conversion
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
