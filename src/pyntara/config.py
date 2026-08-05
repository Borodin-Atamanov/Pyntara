"""Configuration loading from config.toml.

The file at the repository root is the single source of truth for the
Python part of the engine. A missing or invalid file stops the run: there
are no defaults (architecture contract section 3). The composition root
loads the config once and hands it to every task through Context.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when config.toml is missing, unreadable or invalid."""


# The three install modes. They live here rather than in config.toml because
# inst.sh and the desktop detection in the entry point are hard-wired to the
# same names: moving them into the file would create a second source of
# truth for the mode vocabulary.
MODES: tuple[str, ...] = ("minimal", "server", "desktop")


@dataclass(frozen=True)
class EngineConfig:
    """Engine-wide runtime values from the [engine] table."""

    task_data_root: Path
    notice_timeout: int
    command_timeout_seconds: int
    process_check_timeout_seconds: int
    task_start_delay_seconds: float


@dataclass(frozen=True)
class CliToolsConfig:
    """Console utility set installed by the cli_tools task."""

    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int
    package_success_threshold_percent: int


@dataclass(frozen=True)
class AddExtraReposConfig:
    """Ubuntu archive components ensured by the add_extra_repos task."""

    components: tuple[str, ...]


@dataclass(frozen=True)
class SwapfileServiceInstallConfig:
    """Swap file parameters for the swapfile_service_install task.

    The swap size is min(RAM * ram_multiplier + ram_extra_mb,
    free_disk * disk_fraction); ram_multiplier and ram_extra_mb size the
    swap from installed RAM, disk_fraction caps it by free disk space.
    """

    swapfile_path: Path
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float


@dataclass(frozen=True)
class ZswapServiceConfig:
    """Compressed swap cache parameters for the zswap_service task.

    The values are written into /sys/module/zswap/parameters. enabled and
    shrinker_enabled are strict booleans; compressor names the compression
    algorithm; max_pool_percent and accept_threshold_percent are the pool
    ceiling and the re-accept threshold as percentages of RAM and of the
    pool limit.
    """

    enabled: bool
    compressor: str
    max_pool_percent: int
    accept_threshold_percent: int
    shrinker_enabled: bool


@dataclass(frozen=True)
class TaskConfig:
    """One task entry from the [[tasks]] section of config.toml."""

    name: str
    description: str
    depends: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    """Validated content of config.toml."""

    engine: EngineConfig
    cli_tools: CliToolsConfig
    add_extra_repos: AddExtraReposConfig
    swapfile_service_install: SwapfileServiceInstallConfig
    zswap_service: ZswapServiceConfig
    tasks: tuple[TaskConfig, ...]


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


def _engine_table(raw: object) -> EngineConfig:
    """Validate the [engine] table and build EngineConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[engine] section is missing or not a table")
    task_data_root = raw.get("task_data_root")
    if not isinstance(task_data_root, str):
        raise ConfigError("engine.task_data_root must be a string")
    return EngineConfig(
        task_data_root=Path(task_data_root),
        notice_timeout=_int_field(raw.get("notice_timeout"), "engine.notice_timeout"),
        command_timeout_seconds=_int_field(
            raw.get("command_timeout_seconds"), "engine.command_timeout_seconds"
        ),
        process_check_timeout_seconds=_int_field(
            raw.get("process_check_timeout_seconds"),
            "engine.process_check_timeout_seconds",
        ),
        task_start_delay_seconds=_float_field(
            raw.get("task_start_delay_seconds"), "engine.task_start_delay_seconds"
        ),
    )


def _cli_tools_table(raw: object) -> CliToolsConfig:
    """Validate the [cli_tools] table and build CliToolsConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[cli_tools] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("cli_tools.packages must be an array of strings")
    package_success_threshold_percent = _int_field(
        raw.get("package_success_threshold_percent"),
        "cli_tools.package_success_threshold_percent",
    )
    if not 0 <= package_success_threshold_percent <= 100:
        raise ConfigError(
            "cli_tools.package_success_threshold_percent must be between 0 and 100"
        )
    return CliToolsConfig(
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "cli_tools.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"), "cli_tools.package_install_retries"
        ),
        package_success_threshold_percent=package_success_threshold_percent,
    )


def _add_extra_repos_table(raw: object) -> AddExtraReposConfig:
    """Validate the [add_extra_repos] table and build AddExtraReposConfig.

    Components are non-empty strings without whitespace, deduplicated while
    preserving their configured order. An empty list is invalid: an empty
    component set would make the task trivially satisfied.
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
    return AddExtraReposConfig(components=tuple(unique))


def _swapfile_service_install_table(raw: object) -> SwapfileServiceInstallConfig:
    """Validate the [swapfile_service_install] table and build the config.

    swapfile_path is a non-empty string; ram_multiplier is a non-negative
    number; ram_extra_mb is a non-negative integer; disk_fraction must be
    greater than zero and at most one, so the swap size always stays finite
    and positive when RAM and disk are present.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[swapfile_service_install] section is missing or not a table"
        )
    swapfile_path = raw.get("swapfile_path")
    if not isinstance(swapfile_path, str) or not swapfile_path:
        raise ConfigError(
            "swapfile_service_install.swapfile_path must be a non-empty string"
        )
    ram_multiplier = _float_field(
        raw.get("ram_multiplier"), "swapfile_service_install.ram_multiplier"
    )
    ram_extra_mb = _int_field(
        raw.get("ram_extra_mb"), "swapfile_service_install.ram_extra_mb"
    )
    if ram_extra_mb < 0:
        raise ConfigError(
            "swapfile_service_install.ram_extra_mb must not be negative"
        )
    disk_fraction = _float_field(
        raw.get("disk_fraction"), "swapfile_service_install.disk_fraction"
    )
    if not 0 < disk_fraction <= 1:
        raise ConfigError(
            "swapfile_service_install.disk_fraction must be between 0 (exclusive) and 1"
        )
    return SwapfileServiceInstallConfig(
        swapfile_path=Path(swapfile_path),
        ram_multiplier=ram_multiplier,
        ram_extra_mb=ram_extra_mb,
        disk_fraction=disk_fraction,
    )


def _zswap_service_table(raw: object) -> ZswapServiceConfig:
    """Validate the [zswap_service] table and build ZswapServiceConfig.

    enabled and shrinker_enabled are strict booleans; compressor is a
    non-empty string; max_pool_percent and accept_threshold_percent are
    integers between 1 and 100, the meaningful range for a percentage that
    the kernel accepts on the sysfs attributes. A pool ceiling of zero
    would disable zswap entirely, so it is rejected here.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zswap_service] section is missing or not a table")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("zswap_service.enabled must be a boolean")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zswap_service.compressor must be a non-empty string")
    max_pool_percent = _int_field(
        raw.get("max_pool_percent"), "zswap_service.max_pool_percent"
    )
    if not 1 <= max_pool_percent <= 100:
        raise ConfigError(
            "zswap_service.max_pool_percent must be between 1 and 100"
        )
    accept_threshold_percent = _int_field(
        raw.get("accept_threshold_percent"),
        "zswap_service.accept_threshold_percent",
    )
    if not 1 <= accept_threshold_percent <= 100:
        raise ConfigError(
            "zswap_service.accept_threshold_percent must be between 1 and 100"
        )
    shrinker_enabled = raw.get("shrinker_enabled")
    if not isinstance(shrinker_enabled, bool):
        raise ConfigError("zswap_service.shrinker_enabled must be a boolean")
    return ZswapServiceConfig(
        enabled=enabled,
        compressor=compressor,
        max_pool_percent=max_pool_percent,
        accept_threshold_percent=accept_threshold_percent,
        shrinker_enabled=shrinker_enabled,
    )


def _tasks_table(raw: object) -> tuple[TaskConfig, ...]:
    """Validate the [[tasks]] section and build the task catalog.

    The catalog is non-empty; names are unique Python identifiers; every
    dependency names a task listed earlier in the file, which also rules out
    dependency cycles and keeps default task sets ordered; modes are known
    install modes without duplicates.
    """

    if not isinstance(raw, list):
        raise ConfigError("[tasks] section is missing or not an array of tables")
    result: list[TaskConfig] = []
    seen_names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[tasks] entries must be tables")
        name = entry.get("name")
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ConfigError("[tasks] task name must be a non-empty identifier")
        if name in seen_names:
            raise ConfigError(f"[tasks] duplicate task name: {name}")
        seen_names.add(name)
        description = entry.get("description")
        if not isinstance(description, str):
            raise ConfigError(f"[tasks] task {name}: description must be a string")
        depends_raw = entry.get("depends", [])
        if not isinstance(depends_raw, list) or not all(
            isinstance(dep, str) for dep in depends_raw
        ):
            raise ConfigError(
                f"[tasks] task {name}: depends must be an array of strings"
            )
        known_names = {task.name for task in result}
        for dep in depends_raw:
            if dep not in known_names:
                raise ConfigError(
                    f"[tasks] task {name}: dependency {dep!r} must be listed earlier"
                )
        modes_raw = entry.get("modes")
        if not isinstance(modes_raw, list) or not modes_raw:
            raise ConfigError(f"[tasks] task {name}: modes must be a non-empty array")
        if not all(isinstance(mode, str) for mode in modes_raw):
            raise ConfigError(f"[tasks] task {name}: modes must be strings")
        for mode in modes_raw:
            if mode not in MODES:
                raise ConfigError(
                    f"[tasks] task {name}: unknown install mode {mode!r}"
                )
        if len(set(modes_raw)) != len(modes_raw):
            raise ConfigError(f"[tasks] task {name}: duplicate mode entries")
        result.append(
            TaskConfig(
                name=name,
                description=description,
                depends=tuple(depends_raw),
                modes=tuple(modes_raw),
            )
        )
    if not result:
        raise ConfigError("[tasks] section must contain at least one task")
    return tuple(result)


def load_config(path: Path) -> Config:
    """Read and validate config.toml. Raises ConfigError on any problem."""

    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return Config(
        engine=_engine_table(data.get("engine")),
        cli_tools=_cli_tools_table(data.get("cli_tools")),
        add_extra_repos=_add_extra_repos_table(data.get("add_extra_repos")),
        swapfile_service_install=_swapfile_service_install_table(
            data.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(data.get("zswap_service")),
        tasks=_tasks_table(data.get("tasks")),
    )
