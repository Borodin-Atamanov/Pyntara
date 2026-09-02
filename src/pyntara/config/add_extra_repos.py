"""[add_extra_repos] table: Ubuntu archive components and hosts."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _bool_field


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


def _add_extra_repos_table(raw: object) -> AddExtraReposConfig:
    """Validate the [add_extra_repos] table and build AddExtraReposConfig.

    Components are non-empty strings without whitespace, deduplicated while
    preserving their configured order. An empty list is invalid: an empty
    component set would make the task trivially satisfied.
    keep_downloaded_debs is a required boolean, never silently defaulted.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[add_extra_repos] section is missing or not a table")
    components = raw.get("components")
    if not isinstance(components, list) or not components:
        raise ConfigError(
            "add_extra_repos.components must be a non-empty array of strings"
        )
    if not all(
        isinstance(component, str)
        and component
        and component == component.strip()
        and " " not in component
        for component in components
    ):
        raise ConfigError(
            "add_extra_repos.components must be non-empty strings without whitespace"
        )
    unique: list[str] = []
    seen: set[str] = set()
    for component in components:
        if component not in seen:
            seen.add(component)
            unique.append(component)
    ubuntu_hosts = raw.get("ubuntu_hosts")
    if not isinstance(ubuntu_hosts, list) or not ubuntu_hosts:
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be a non-empty array of strings"
        )
    if not all(
        isinstance(host, str) and host and host == host.strip()
        for host in ubuntu_hosts
    ):
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be non-empty strings"
        )
    keep_downloaded_debs = _bool_field(
        raw.get("keep_downloaded_debs"), "add_extra_repos.keep_downloaded_debs"
    )
    return AddExtraReposConfig(
        components=tuple(unique),
        ubuntu_hosts=tuple(ubuntu_hosts),
        keep_downloaded_debs=keep_downloaded_debs,
    )
