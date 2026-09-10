"""Field-level validation helpers shared by every config table parser.

The helpers validate one raw TOML value and either return the typed value
or raise ConfigError. The vocabulary constants live here too: they are
part of the config contract and are validated against in the parsers.
"""


from __future__ import annotations


class ConfigError(RuntimeError):
    """Raised when config.toml is missing, unreadable or invalid."""


MODES: tuple[str, ...] = ("minimal", "server", "desktop")


SEND_ORDERS: tuple[str, ...] = ("oldest_first", "newest_first")


I2PD_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warn", "error", "none")


TOR_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warn", "err")


DNS_OVER_TLS_VALUES: tuple[str, ...] = ("yes", "opportunistic", "no")


YGGDRASIL_LISTEN_SCHEMES: tuple[str, ...] = ("tcp", "tls", "quic", "ws", "unix")


YGGDRASIL_PEER_SCHEMES: tuple[str, ...] = (
    "tcp",
    "tls",
    "quic",
    "ws",
    "wss",
    "socks",
    "sockstls",
    "unix",
)


NUMLOCK_STATES: tuple[str, ...] = ("on", "off", "unchanged")


CLICK_METHODS: tuple[str, ...] = ("clickfinger", "clickareas", "none")


SHARE_ADDR_STRATEGIES: tuple[str, ...] = ("node", "listen", "custom")
