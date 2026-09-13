"""[add_extra_repos] table: Ubuntu archive components and hosts."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AddExtraReposConfig:
    """Ubuntu archive components, hosts and apt retention managed by the task.

    components are the archive components ensured in every Ubuntu section;
    ubuntu_hosts are the official archive hosts whose source files the task
    may rewrite. A source file matching none of the hosts is third-party
    and left untouched. legacy_sources_file and sources_list_d are the apt
    sources the task reads and rewrites; keep_debs_file is the apt drop-in
    the task owns while keep_downloaded_debs is true, which keeps
    downloaded .deb files after install, and keep_debs_dropin_content is
    the four-line body that drop-in carries. uris_field_name and
    components_field_name are the names of the two deb822 fields of a
    .sources file the task reads, compared without case, so a distribution
    that renames a field is answered in the config.
    """

    components: tuple[str, ...]
    ubuntu_hosts: tuple[str, ...]
    uris_field_name: str
    components_field_name: str
    keep_downloaded_debs: bool
    legacy_sources_file: Path
    sources_list_d: Path
    keep_debs_file: Path
    keep_debs_dropin_content: str
