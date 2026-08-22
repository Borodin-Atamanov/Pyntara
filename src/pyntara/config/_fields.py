"""Field-level validation helpers shared by every config table parser.

The helpers validate one raw TOML value and either return the typed value
or raise ConfigError. The vocabulary constants live here too: they are
part of the config contract and are validated against in the parsers.
"""

from __future__ import annotations


class ConfigError(RuntimeError):
    """Raised when config.toml is missing, unreadable or invalid."""


# The three install modes. They live here rather than in config.toml because
# inst.sh and the desktop detection in the entry point are hard-wired to the
# same names: moving them into the file would create a second source of
# truth for the mode vocabulary.
MODES: tuple[str, ...] = ("minimal", "server", "desktop")

# Allowed values of system_metrics_setup.send_order. The vocabulary is part
# of the config contract and therefore validated here, like MODES.
SEND_ORDERS: tuple[str, ...] = ("oldest_first", "newest_first")

# Allowed values of i2pd_service_setup.log_level. The vocabulary is part of
# the config contract and therefore validated here, like MODES; the values
# are the log levels i2pd accepts in its configuration file.
I2PD_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warn", "error", "none")

# Allowed values of tor_setup.log_level. The vocabulary is part of the
# config contract and therefore validated here, like MODES; the values are
# the log levels tor accepts in its configuration file.
TOR_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "notice", "warn", "err")

# Allowed values of nextdns_setup_system_wide.dns_over_tls. The vocabulary
# is part of the config contract and therefore validated here, like MODES;
# the values are the DNSOverTLS modes systemd-resolved accepts.
DNS_OVER_TLS_VALUES: tuple[str, ...] = ("yes", "opportunistic", "no")

# URI schemes yggdrasil accepts in Listen: wss is not a listener (the
# source rejects it), socks and sockstls are outgoing-only.
YGGDRASIL_LISTEN_SCHEMES: tuple[str, ...] = ("tcp", "tls", "quic", "ws", "unix")

# URI schemes yggdrasil accepts in Peers: the full outbound set.
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


def _int_field(raw: object, name: str) -> int:
    """Validate an integer config value; bool is a subclass of int and must
    be excluded explicitly."""

    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(f"{name} must be an integer")
    return raw


def _float_field(raw: object, name: str) -> float:
    """Validate a numeric config value; bool is a subclass of int and must
    be excluded explicitly."""

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"{name} must be a number")
    value = float(raw)
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


def _octal_mode_field(raw: object, name: str) -> int:
    """Parse one octal file mode string like "0700" into an int.

    TOML has no octal literals, so the modes are configured as strings
    and converted here; a value that is not four octal digits is a
    config error.
    """

    if not isinstance(raw, str) or len(raw) != 4:
        raise ConfigError(f"{name} must be an octal string like '0700'")
    try:
        parsed = int(raw, 8)
    except ValueError:
        raise ConfigError(f"{name} must be an octal string like '0700'") from None
    return parsed


def _nonempty_string_field(raw: object, name: str) -> str:
    """Validate a non-empty string config value."""

    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{name} must be a non-empty string")
    return raw


def _string_list(raw: object, name: str) -> tuple[str, ...]:
    """Validate a non-empty array of non-empty strings."""

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name} must be a non-empty array of strings")
    if not all(isinstance(part, str) and part.strip() for part in raw):
        raise ConfigError(f"{name} must be non-empty strings")
    return tuple(part.strip() for part in raw)


def _bool_field(raw: object, name: str) -> bool:
    """Validate a boolean config value."""

    if not isinstance(raw, bool):
        raise ConfigError(f"{name} must be a boolean")
    return raw


def _string_map(raw: object, name: str) -> dict[str, str]:
    """Validate a table of non-empty strings keyed by non-empty strings.

    The keys and values are stripped of surrounding whitespace. An empty
    table is allowed (it means the feature the map configures is off).
    """

    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a table of strings")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{name} keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{name} values must be non-empty strings")
        result[key.strip()] = value.strip()
    return result
