"""Values of the cli_tools_heavy_setup task.

The media and document toolset installed from the Ubuntu archive: images,
audio, video, PDF, ebooks and OCR. It is the long half of the console tools,
separated from the everyday utilities of cli_tools_lite_setup, so a long or
failing install here never holds back the light set.

The four heaviest packages of the toolset are left out of the list and named in
the comments below together with the size and the time measured in the install
logs: in a real run they took about 1305 MB of the 1401 MB and about 462 s of
the 655 s this section installed, so they were the whole cost of it. A machine
that needs one of them installs it by hand.

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

# The media and document tools. A name here must be the real package, because
# dpkg-query cannot see a virtual name: a virtual name would look missing
# forever and be reinstalled on every run.
PACKAGES: tuple[str, ...] = (
    # images
    "webp",  # cwebp and img2webp, WebP encoder and animation
    "libheif-examples",  # heif-enc and heif-convert, HEIC and HEIF encoding
    "jpegoptim",  # optimize the jpeg file size
    "pngquant",  # lossy PNG compressor with palette reduction and dithering
    "jhead",  # jpeg EXIF header tool with autorotate
    "libjpeg-turbo-progs",  # jpegtran, lossless jpeg operations
    "exiftran",  # lossless jpeg rotation by EXIF
    # audio and video
    "mediainfo",  # technical details of media files, codecs and streams
    "mkvtoolnix",  # mkvmerge and mkvextract, MKV muxing and extraction
    "eyed3",  # eyeD3, mp3 tag editor
    # metadata
    "libimage-exiftool-perl",  # exiftool; the real package behind the virtual name
    "mat2",  # strip metadata from files
    # pdf and documents
    "poppler-utils",  # pdfunite, pdfseparate, pdfattach, pdfinfo, pdftotext, pdftoppm
    # The four heaviest packages of the whole toolset are left out because they
    # are too heavy and take too much disk. The numbers measured on the install
    # logs (median of eight real runs, the additional disk space apt reported
    # for the package together with its dependencies, and the time apt took)
    # are 528 MB and 217 s for texlive-extra-utils, 316 MB and 189 s for
    # calibre, 254 MB and 32 s for pdftk-java, 207 MB and 24 s for pandoc.
    # Together they are 1305 MB of the 1401 MB and 462 s of the 655 s the whole
    # section installed, and every one of them drags a whole toolchain: a TeX
    # distribution, a Qt and Python media stack, a Java runtime. A machine that
    # needs one of them installs it by hand.
    # "texlive-extra-utils",  # pdfcrop, crop pdf margins
    # "pdftk-java",  # pdftk, pdf manipulation and metadata
    "qpdf",  # pdf encryption, cleaning and normalization
    "ghostscript",  # gs, postscript and pdf interpreter
    # "pandoc",  # document format converter
    # "calibre",  # ebook-convert, ebook format conversion
    # ocr
    "tesseract-ocr",  # OCR engine
    "tesseract-ocr-eng",  # English language data for tesseract
    "tesseract-ocr-rus",  # Russian language data for tesseract
    "tesseract-ocr-spa",  # Spanish language data for tesseract
    # network scanning, moved here from cli_tools_lite_setup because its
    # install with dependencies took 28 MB and 22 s, the largest install of
    # that section
    "nmap",  # network scanner
)

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error. The pair of package install values
# comes from the shared module common.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "PACKAGE_SUCCESS_THRESHOLD_PERCENT",
)
