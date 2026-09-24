"""Values of the add_extra_repos task.

A fresh Kubuntu enables only the main component of the Ubuntu archive, so the
task appends the other components to every archive section whose URIs point to
an official Ubuntu host and never touches a third-party source. Independent of
the components work it also keeps an apt drop-in that stops apt and
unattended-upgrades from deleting downloaded .deb files after a successful
install.
"""

from __future__ import annotations

from pathlib import Path

# Components the task ensures in every Ubuntu archive section of the apt
# sources. main is always present on a fresh Kubuntu; the other three are
# enabled here.
COMPONENTS: tuple[str, ...] = ("main", "universe", "restricted", "multiverse")

# Official Ubuntu archive hosts the task may manage. A source file whose URIs
# match none of these hosts is third-party and left untouched.
UBUNTU_HOSTS: tuple[str, ...] = (
    "archive.ubuntu.com",
    "security.ubuntu.com",
    "ports.ubuntu.com",
    "old-releases.ubuntu.com",
)

# Names of the two deb822 fields the task reads in a .sources file: the field
# that carries the archive URIs and the field that lists the enabled
# components. The comparison is without case, so a distribution that renames a
# field is answered here.
URIS_FIELD_NAME: str = "uris:"
COMPONENTS_FIELD_NAME: str = "components:"

# The apt sources the task manages: the legacy single file and the drop-in
# directory. The directory holds apt sources only in the two formats below; a
# file with any other extension, or with an upper case name, is left alone.
LEGACY_SOURCES_FILE: Path = Path("/etc/apt/sources.list")
SOURCES_LIST_D: Path = Path("/etc/apt/sources.list.d")

# Suffix of a one-line sources file (one deb line per entry), the format of the
# legacy sources file.
LEGACY_SOURCE_SUFFIX: str = ".list"

# Keywords that open a one-line source line, with the space that separates the
# keyword from the rest of the line, so a word that merely starts with the
# letters of a keyword is not taken for a source. The task rewrites the lines
# of these kinds.
LEGACY_SOURCE_TYPE_KEYWORDS: tuple[str, ...] = ("deb ", "deb-src ")

# URL schemes that mark the archive URI token of a one-line source line. The
# task reads the tokens after the URI and the suite as the components, so a
# mirror scheme the operator adds here is understood instead of refused.
SOURCE_URL_SCHEMES: tuple[str, ...] = ("http://", "https://")

# Suffix of a deb822 sources file, whose entries are fields. The task reads
# such a file field by field instead of line by line.
DEB822_SOURCE_SUFFIX: str = ".sources"

# The apt drop-in that tells apt and unattended-upgrades what to do with the
# packages they download. The task owns this file completely: it writes this
# body with the answer of the run filled in, so the machine carries an explicit
# answer instead of falling back to the default of apt, which is to keep every
# downloaded package. The option names stand here once.
KEEP_DEBS_FILE: Path = Path("/etc/apt/apt.conf.d/99keep-debs.conf")

# Body of that drop-in, written exactly as it stands here with {value} replaced
# by the answer of the run. A file that already matches is left alone, so the
# task does not rewrite a machine that carries the same body.
KEEP_DEBS_DROPIN_TEMPLATE: str = (
    "# Written by pyntara add_extra_repos\n"
    'APT::Keep-Downloaded-Packages "{value}";\n'
    'Unattended-Upgrade::Keep-Debs-After-Install "{value}";\n'
)

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "COMPONENTS",
    "UBUNTU_HOSTS",
    "URIS_FIELD_NAME",
    "COMPONENTS_FIELD_NAME",
    "LEGACY_SOURCES_FILE",
    "SOURCES_LIST_D",
    "LEGACY_SOURCE_SUFFIX",
    "LEGACY_SOURCE_TYPE_KEYWORDS",
    "SOURCE_URL_SCHEMES",
    "DEB822_SOURCE_SUFFIX",
    "KEEP_DEBS_FILE",
    "KEEP_DEBS_DROPIN_TEMPLATE",
)
