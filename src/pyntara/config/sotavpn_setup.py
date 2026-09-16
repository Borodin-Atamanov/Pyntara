"""[sotavpn_setup] table: the Sotavpn pool of the local proxy."""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SotavpnSetupConfig:
    """Sotavpn pool parameters for the sotavpn_setup task.

    The task turns the paid Sota Connect account of the source vault into
    a source of remote exits of the 3x-ui panel: the bridge of the
    Sotavpn repository runs for the desktop user and serves the server
    list of the account on a subscription address, the panel subscribes
    to that address, and the nodes the panel fetches join the pool of the
    local proxy that the three_x_ui_xray_setup task built
    (docs/spec/sotavpn-setup.md).

    The account side: username and home_dir name the desktop user the
    bridge runs as, runuser_command is the wrapper that runs a command as
    that user, and key_entry_title names the source vault entry whose
    password carries the access key of the account.

    The bridge installation: archive_url is the archive of the repository
    the task downloads each run, archive_temp_prefix and
    archive_temp_suffix name the downloaded file, installer_file_name is
    the installer inside the archive and installer_command the command
    that runs it ({python} is filled from the engine interpreter and
    {installer_path} with the path of the extracted installer).
    service_unit_name is the user unit the installer creates and
    user_service_is_active_command reads its state through the user
    manager of the account. user_install_relative_path and
    settings_file_name locate the installed settings file, from which
    settings_version_key and settings_http_port_key name the lines the
    task reads (the program version and the plain HTTP port), so neither
    value is held in this config. The installer runs with the session
    environment the engine reads for the desktop user, so the user manager
    of that account is reachable from a run that has no session of its
    own.

    The panel side: subscription_url_template is the address the panel
    subscribes to ({port} and {key} are filled in), and the subscription_
    fields are the outbound subscription the task creates: its label, the
    refresh interval in seconds and the flags of the call, of which
    allow_private is what lets the panel fetch from the loopback address.
    The task owns no pool: the client half of the panel, the pool that
    carries the remote classes included, is built by the
    three_x_ui_xray_setup task, and the nodes of this subscription join
    that pool because the panel names them with the prefix the pool
    covers (pool_member_prefix of that section).
    subscription_fetch_wait_seconds bounds the wait for the panel to
    fetch the list after the refresh call. bridge_ready_wait_seconds and
    readiness_check_delay_seconds bound the wait for the bridge listener
    after an installation.
    """

    username: str
    home_dir: str
    runuser_command: tuple[str, ...]
    archive_url: str
    archive_temp_prefix: str
    archive_temp_suffix: str
    installer_file_name: str
    installer_command: tuple[str, ...]
    service_unit_name: str
    user_service_is_active_command: tuple[str, ...]
    user_install_relative_path: str
    settings_file_name: str
    settings_version_key: str
    settings_http_port_key: str
    key_entry_title: str
    subscription_url_template: str
    subscription_remark: str
    subscription_update_interval_seconds: int
    subscription_enabled: bool
    subscription_allow_private: bool
    subscription_allow_insecure: bool
    subscription_prepend: bool
    subscription_fetch_wait_seconds: int
    bridge_ready_wait_seconds: int
    readiness_check_delay_seconds: int
