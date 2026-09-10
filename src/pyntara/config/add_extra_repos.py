"""[add_extra_repos] table: Ubuntu archive components and hosts."""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AddExtraReposConfig:
    """Ubuntu archive components, hosts and apt retention managed by the task.

    components are the archive components ensured in every Ubuntu section;
    ubuntu_hosts are the official archive hosts whose source files the task
    may rewrite. A source file matching none of the hosts is third-party
    and left untouched. keep_downloaded_debs, when true, makes the task
    write the apt drop-in that keeps downloaded .deb files after install.
    """

    components: tuple[str, ...]
    ubuntu_hosts: tuple[str, ...]
    keep_downloaded_debs: bool
