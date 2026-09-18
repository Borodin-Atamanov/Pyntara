"""Values of the task catalog.

The catalog is the list of tasks the engine can run. Each record names the task,
the sentence the run shows for it, the tasks it depends on and the install modes
it belongs to, so one edit in this file adds a task to the engine
(docs/TODO.md, stage E). The mode vocabulary lives here too, because the catalog
is the only reader of it.

The catalog is data of the run itself and not of a task section, which is why it
lives beside the engine values rather than in a section module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    """One task of the catalog: its name, its description and its place.

    depends names the tasks that must run before it, and modes names the install
    modes the task belongs to; an empty depends means the task can run first and
    an empty modes means no mode selects it.
    """

    name: str
    description: str
    depends: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


# The install modes: production reads the vocabulary to accept or refuse a mode,
# and every catalog record names the modes it belongs to.
MODES: tuple[str, ...] = ("minimal", "server", "desktop")

# The task catalog, in the order the run resolves it.
CATALOG: tuple[TaskSpec, ...] = (
    TaskSpec(
        "add_extra_repos",
        "Enable extra Ubuntu archive components: universe, restricted, multiverse.",
        (),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "hostname",
        "Generate and persist random hostname.",
        (),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "swapfile_service_install",
        "Calculate and configure swapfile from RAM and free disk space.",
        (),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "zram_service",
        "Configure aggressive ZRAM by CPU and RAM.",
        ("swapfile_service_install",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "zswap_service",
        "Configure aggressive zswap compressed swap cache with zstd.",
        ("swapfile_service_install",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "local_vault_setup",
        "Setup local vault for secrets storage",
        ("zram_service",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "system_metrics_setup",
        "Deploy and run the System Metrics service with periodic vault checks.",
        ("local_vault_setup",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "ssh_daemon_setup",
        "Install and configure SSH service with passwordless login keys.",
        (),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "ssh_client_setup",
        "Configure system-wide SSH client defaults through a drop-in.",
        (),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "port_forwarding_setup",
        "Deploy the Auto Port Forwarding service that keeps reverse ssh tunnels "
        "to the vault port-forwarding servers.",
        ("ssh_daemon_setup", "local_vault_setup", "system_metrics_setup", "hostname"),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "kde_keyboard_setup",
        "Configure KDE keyboard layouts and the layout indicator.",
        (),
        ("desktop",),
    ),
    TaskSpec(
        "kde_settings",
        "Set the dark color scheme and the dark global theme for KDE.",
        (),
        ("desktop",),
    ),
    TaskSpec(
        "vocalinux_setup",
        "Install Vocalinux voice dictation from the AppImage release and "
        "configure it for the desktop user.",
        ("add_extra_repos",),
        ("desktop",),
    ),
    TaskSpec(
        "nextdns_setup_system_wide",
        "Select and record the machine's NextDNS profile without configuring a "
        "resolver.",
        ("local_vault_setup", "hostname"),
        (),
    ),
    TaskSpec(
        "dnsproxy_setup",
        "Run dnsproxy as the system-wide DNS resolver with NextDNS and fallback "
        "servers.",
        ("local_vault_setup", "hostname", "nextdns_setup_system_wide"),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "i2pd_service_setup",
        "Install and configure the latest i2pd from GitHub releases as a system "
        "service.",
        ("add_extra_repos",),
        ("server", "desktop"),
    ),
    TaskSpec(
        "yggdrasil_service_setup",
        "Install the latest yggdrasil from GitHub releases as a system service.",
        (),
        ("server", "desktop"),
    ),
    TaskSpec(
        "tor_setup",
        "Install Tor and publish the SSH service as an onion service.",
        ("add_extra_repos",),
        ("server", "desktop"),
    ),
    TaskSpec(
        "three_x_ui_xray_setup",
        "Install the 3x-ui Xray panel as a system service, serve the universal "
        "server inbound, and make this machine a client of the remote server: a "
        "local proxy inbound plus the routing policy that sends each connection "
        "to the right outbound.",
        ("yggdrasil_service_setup", "tor_setup", "i2pd_service_setup"),
        ("server", "desktop"),
    ),
    TaskSpec(
        "sotavpn_setup",
        "Install the Sotavpn bridge for the desktop user, subscribe the panel to "
        "the server list of the paid account, and point the remote classes of the "
        "local proxy at the least-ping pool of the Sota nodes and the remote "
        "server.",
        ("three_x_ui_xray_setup",),
        ("server", "desktop"),
    ),
    TaskSpec(
        "rustdesk_setup",
        "Install and configure the RustDesk remote desktop client with a "
        "per-machine password.",
        ("local_vault_setup",),
        ("desktop",),
    ),
    TaskSpec(
        "imagemagick_setup",
        "Install ImageMagick from the Ubuntu archive and deploy the tuned "
        "security policy.",
        ("add_extra_repos",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "ffmpeg_setup",
        "Install ffmpeg from the Ubuntu archive.",
        ("add_extra_repos",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "upnp_forwarding_setup",
        "Deploy the service and timer that ask the home router through UPnP to "
        "forward the SSH port of this machine.",
        ("ssh_daemon_setup", "system_metrics_setup", "hostname"),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "system_metrics_initial_collect",
        "Run the system metrics collector once to send the network report right "
        "after provisioning.",
        ("system_metrics_setup",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "chrome_setup",
        "Install Google Chrome, apply the browser settings from the "
        "chromium-default-settings repository, and start the browser through the "
        "local Xray proxy with the DevTools listener.",
        ("three_x_ui_xray_setup",),
        ("desktop",),
    ),
    TaskSpec(
        "playwright_setup",
        "Install the playwright-cli browser control tool for the desktop user.",
        ("add_extra_repos", "chrome_setup"),
        ("desktop",),
    ),
    TaskSpec(
        "cli_tools",
        "Install curated console utilities: file managers, system and media tools.",
        ("add_extra_repos",),
        ("minimal", "server", "desktop"),
    ),
    TaskSpec(
        "telegram_setup",
        "Install the latest Telegram Desktop for the desktop user with "
        "auto-update.",
        (),
        ("desktop",),
    ),
    TaskSpec(
        "scrcpy_setup",
        "Install the scrcpy Android screen mirroring client for the desktop user "
        "from the GitHub release, with the Ubuntu archive as the fallback.",
        ("add_extra_repos",),
        ("desktop",),
    ),
    TaskSpec(
        "commit_final_system_metrics",
        "Commit the runtime vault and the encrypted telemetry PDF into System "
        "Metrics.",
        ("system_metrics_setup",),
        ("minimal", "server", "desktop"),
    ),
)

# The names the run reads. The list lives next to the values it names.
READ_VALUE_NAMES: tuple[str, ...] = (
    "MODES",
    "CATALOG",
)
