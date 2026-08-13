# Yggdrasil service

There is a dedicated yggdrasil installation task: yggdrasil_service_setup.

The task installs the yggdrasil network router from the GitHub releases of the configured repository and runs it as a system service. The distribution package is never used, so the installed version is always the newest release instead of the version packaged for the distribution.

## Version resolution

The newest release tag comes from the GitHub releases API of the configured repository: the endpoint https://api.github.com/repos/{github_repo}/releases/latest returns the latest non-prerelease release, and tag_name is the version. The release is fetched with curl and parsed as JSON; a failed request, unparsable payload or a missing tag_name is reported as a task error. The tag carries a leading v (v0.5.14), the release assets and the version output do not, so the tag is used with the leading v stripped.

The installed version comes from yggdrasil -version: the first dotted version triple in the combined stdout and stderr output. A missing binary, a nonzero exit or a hung query reports the version as not installed, so the task reinstalls. When the installed version differs from the newest release version, the task downloads and installs the new release; the rerun after a new upstream release therefore updates yggdrasil and restarts the service, which is the intended consequence of always running the newest version.

## Operating system and architecture

The architecture is read with dpkg --print-architecture through the shared helper in pyntara.utils. The architecture part of the asset name matches the dpkg architecture, so the asset yggdrasil-{version}-{arch}.deb is chosen directly by name from the release payload; a release without the asset for the architecture is reported as a task error. The distribution family is not checked: the deb package depends only on systemd, so the install fails loudly on a non-Debian system at the dpkg query itself, and the codename plays no role in the asset name.

## Download trust

The package is downloaded from the official GitHub release assets of the configured repository. No checksum verification is performed: the source is trusted, and an extra check would add a failure point without protecting the install, because the checksum file travels over the same channel as the package. The download uses curl --fail and a nonzero exit is reported as a task error, so a failed transfer is never mistaken for a successful one.

## Configuration ownership

The package owns the configuration and the node keys. Its postinst creates the group and the /etc/yggdrasil directory, generates /etc/yggdrasil/yggdrasil.conf with a fresh key pair when the file is absent, normalizes it on updates and enables and starts the service. The task never writes the configuration, so a reinstall keeps the node identity: overwriting the file would replace the keys and change the node address.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The package postinst enables the unit; the task enables it when it is not enabled, then starts the unit when it is inactive or restarts it when it is active and the package changed or the task runs in force mode. After a start or restart the task checks once that the unit reports active, because the simple service either starts or fails immediately; a unit that stays inactive is a task error. The package also installs the yggdrasil-default-config.service unit; the task does not manage it.

## Idempotency

The target state is reached when the installed version equals the newest release version and the service is enabled and active; the task then skips with changed=False. Force mode restarts the service, but never reinstalls a matching version. The download directory holds only the files of an interrupted install: the package is removed after a successful install, so the directory never accumulates old versions.

## Parameters

All parameters live in config.toml under [yggdrasil_service_setup]:

github_repo is the GitHub repository in owner/name form
download_dir is the directory for the downloaded package file
service_unit_name is the systemd unit installed by the package
install_retries is the retry count of the package install; total attempts are retries plus one

The apt index is never refreshed before the install: the package depends only on systemd, which is always installed, so a refresh would add a failure point without resolving anything.

The task belongs to the server and desktop modes and has no dependencies: it does not touch the apt index, so add_extra_repos is not required.
