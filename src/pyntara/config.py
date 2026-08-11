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
    """Engine-wide runtime values from the [engine] table.

    desktop_detect_processes are the process names whose presence marks a
    desktop session in the default mode detection; the list lives here so
    the detection is configurable without code changes.
    """

    task_data_root: Path
    notice_timeout: int
    command_timeout_seconds: int
    process_check_timeout_seconds: int
    task_start_delay_seconds: float
    desktop_detect_processes: tuple[str, ...]


@dataclass(frozen=True)
class CliToolsConfig:
    """Console utility set installed by the cli_tools task."""

    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int
    package_success_threshold_percent: int


@dataclass(frozen=True)
class AddExtraReposConfig:
    """Ubuntu archive components and hosts managed by add_extra_repos.

    components are the archive components ensured in every Ubuntu section;
    ubuntu_hosts are the official archive hosts whose source files the task
    may rewrite. A source file matching none of the hosts is third-party
    and left untouched.
    """

    components: tuple[str, ...]
    ubuntu_hosts: tuple[str, ...]


@dataclass(frozen=True)
class SwapfileServiceInstallConfig:
    """Swap file parameters for the swapfile_service_install task.

    The swap size is min(RAM * ram_multiplier + ram_extra_mb,
    free_disk * disk_fraction); ram_multiplier and ram_extra_mb size the
    swap from installed RAM, disk_fraction caps it by free disk space.
    swapfile_mode is the octal file mode of the created swapfile;
    size_tolerance_mb is the accepted deviation between the existing and
    the target swap size in mebibytes, so a swapfile resized by rounding
    is not recreated.
    """

    swapfile_path: Path
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float
    swapfile_mode: int
    size_tolerance_mb: int


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
class ZramServiceConfig:
    """Aggressive in-memory swap parameters for the zram_service task.

    The device count equals the CPU core count (fallback_cpu_count when it
    cannot be determined); the total capacity is memory_fraction_percent of
    installed RAM split evenly across the devices and rounded down to the
    alignment_bytes zram page size. Every device uses the compressor
    algorithm and is activated with swap_priority, so ZRAM swap is
    preferred over the disk swapfile.
    """

    compressor: str
    swap_priority: int
    memory_fraction_percent: int
    fallback_cpu_count: int
    alignment_bytes: int


@dataclass(frozen=True)
class SystemMetricsSetupConfig:
    """Runtime parameters of the long-running System Metrics service.

    The section is read by the deployed service on the target machine
    through pyntara.config.load_config, the same loader the installer
    uses: system_config_path is the single config of the system.
    check_interval_seconds is the pause between two vault availability
    checks; python_version selects the interpreter for the deployed
    venv; error_priority and success_priority are the syslog levels of
    failed and successful checks; venv_dir and system_config_path are
    the deployment locations on the target machine. The current
    placeholder check is replaced by the real System Metrics logic in a
    later stage (docs/spec/system-metrics.md).
    """

    check_interval_seconds: int
    python_version: str
    error_priority: int
    success_priority: int
    venv_dir: Path
    system_config_path: Path


@dataclass(frozen=True)
class VaultEntry:
    """One entry of the [vault_structure] table.

    title names the KeePass entry; notes carries the explanatory text that
    the regeneration tooling stores in the notes field of the entry.
    """

    title: str
    notes: str


@dataclass(frozen=True)
class VaultStructureConfig:
    """KeePass vault layout described in the [vault_structure] table.

    The table is the single source of truth for the vault structure
    (docs/spec/secrets-model.md): the structure is flat, every entry lives
    in the root group and is identified by its unique title; notes
    explains what the entry carries and who consumes it.
    """

    entries: tuple[VaultEntry, ...]


@dataclass(frozen=True)
class LocalVaultSetupConfig:
    """Runtime secret vault parameters for the local_vault_setup task.

    source_vault_production and source_vault_default are repository-root
    relative paths to the KeePass databases whose copy becomes the runtime
    vault; local_vault_path and pass_file_path are the absolute target
    locations fixed by docs/spec/secrets-model.md; vault_password_entry_title
    names the source vault entry (from the [vault_structure] table) that
    carries the future local vault password.
    """

    source_vault_production: Path
    source_vault_default: Path
    local_vault_path: Path
    pass_file_path: Path
    vault_password_entry_title: str
    secrets_dir_mode: int
    local_vault_file_mode: int
    pass_dir_mode: int
    pass_file_mode: int
    error_priority: int


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
    zram_service: ZramServiceConfig
    system_metrics_setup: SystemMetricsSetupConfig
    vault_structure: VaultStructureConfig
    local_vault_setup: LocalVaultSetupConfig
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


def _engine_table(raw: object) -> EngineConfig:
    """Validate the [engine] table and build EngineConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[engine] section is missing or not a table")
    task_data_root = raw.get("task_data_root")
    if not isinstance(task_data_root, str):
        raise ConfigError("engine.task_data_root must be a string")
    desktop_detect_processes = raw.get("desktop_detect_processes")
    if not isinstance(desktop_detect_processes, list) or not desktop_detect_processes:
        raise ConfigError(
            "engine.desktop_detect_processes must be a non-empty array of strings"
        )
    if not all(
        isinstance(process, str) and process and process == process.strip()
        for process in desktop_detect_processes
    ):
        raise ConfigError(
            "engine.desktop_detect_processes must be non-empty strings"
        )
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
        desktop_detect_processes=tuple(desktop_detect_processes),
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
    return AddExtraReposConfig(components=tuple(unique), ubuntu_hosts=tuple(ubuntu_hosts))


def _swapfile_service_install_table(raw: object) -> SwapfileServiceInstallConfig:
    """Validate the [swapfile_service_install] table and build the config.

    swapfile_path is a non-empty string; ram_multiplier is a non-negative
    number; ram_extra_mb is a non-negative integer; disk_fraction must be
    greater than zero and at most one, so the swap size always stays finite
    and positive when RAM and disk are present. swapfile_mode is an octal
    string like "0600"; size_tolerance_mb is a non-negative integer.
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
    size_tolerance_mb = _int_field(
        raw.get("size_tolerance_mb"), "swapfile_service_install.size_tolerance_mb"
    )
    if size_tolerance_mb < 0:
        raise ConfigError(
            "swapfile_service_install.size_tolerance_mb must not be negative"
        )
    return SwapfileServiceInstallConfig(
        swapfile_path=Path(swapfile_path),
        ram_multiplier=ram_multiplier,
        ram_extra_mb=ram_extra_mb,
        disk_fraction=disk_fraction,
        swapfile_mode=_octal_mode_field(
            raw.get("swapfile_mode"), "swapfile_service_install.swapfile_mode"
        ),
        size_tolerance_mb=size_tolerance_mb,
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


def _zram_service_table(raw: object) -> ZramServiceConfig:
    """Validate the [zram_service] table and build ZramServiceConfig.

    compressor is a non-empty string; swap_priority is a positive swap
    priority; memory_fraction_percent is a percentage between 1 and 100;
    fallback_cpu_count is at least 1; alignment_bytes is positive, because
    the zram driver rejects a non-positive or unaligned disksize.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zram_service] section is missing or not a table")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zram_service.compressor must be a non-empty string")
    swap_priority = _int_field(
        raw.get("swap_priority"), "zram_service.swap_priority"
    )
    if swap_priority < 1:
        raise ConfigError("zram_service.swap_priority must be positive")
    memory_fraction_percent = _int_field(
        raw.get("memory_fraction_percent"),
        "zram_service.memory_fraction_percent",
    )
    if not 1 <= memory_fraction_percent <= 100:
        raise ConfigError(
            "zram_service.memory_fraction_percent must be between 1 and 100"
        )
    fallback_cpu_count = _int_field(
        raw.get("fallback_cpu_count"), "zram_service.fallback_cpu_count"
    )
    if fallback_cpu_count < 1:
        raise ConfigError("zram_service.fallback_cpu_count must be at least 1")
    alignment_bytes = _int_field(
        raw.get("alignment_bytes"), "zram_service.alignment_bytes"
    )
    if alignment_bytes < 1:
        raise ConfigError("zram_service.alignment_bytes must be positive")
    return ZramServiceConfig(
        compressor=compressor,
        swap_priority=swap_priority,
        memory_fraction_percent=memory_fraction_percent,
        fallback_cpu_count=fallback_cpu_count,
        alignment_bytes=alignment_bytes,
    )


def _system_metrics_setup_table(raw: object) -> SystemMetricsSetupConfig:
    """Validate the [system_metrics_setup] table and build the config.

    check_interval_seconds is a positive integer; a zero or negative
    interval would busy-loop the service. python_version is a non-empty
    string; error_priority and success_priority are syslog levels between
    0 and 7; venv_dir and system_config_path are non-empty strings.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[system_metrics_setup] section is missing or not a table"
        )
    check_interval_seconds = _int_field(
        raw.get("check_interval_seconds"),
        "system_metrics_setup.check_interval_seconds",
    )
    if check_interval_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.check_interval_seconds must be positive"
        )
    python_version = raw.get("python_version")
    if not isinstance(python_version, str) or not python_version:
        raise ConfigError(
            "system_metrics_setup.python_version must be a non-empty string"
        )
    error_priority = _int_field(
        raw.get("error_priority"), "system_metrics_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "system_metrics_setup.error_priority must be between 0 and 7"
        )
    success_priority = _int_field(
        raw.get("success_priority"), "system_metrics_setup.success_priority"
    )
    if not 0 <= success_priority <= 7:
        raise ConfigError(
            "system_metrics_setup.success_priority must be between 0 and 7"
        )
    venv_dir = raw.get("venv_dir")
    if not isinstance(venv_dir, str) or not venv_dir:
        raise ConfigError(
            "system_metrics_setup.venv_dir must be a non-empty string"
        )
    system_config_path = raw.get("system_config_path")
    if not isinstance(system_config_path, str) or not system_config_path:
        raise ConfigError(
            "system_metrics_setup.system_config_path must be a non-empty string"
        )
    return SystemMetricsSetupConfig(
        check_interval_seconds=check_interval_seconds,
        python_version=python_version,
        error_priority=error_priority,
        success_priority=success_priority,
        venv_dir=Path(venv_dir),
        system_config_path=Path(system_config_path),
    )


def _vault_structure_table(raw: object) -> VaultStructureConfig:
    """Validate the [vault_structure] table and build VaultStructureConfig.

    The section is mandatory and non-empty; every entry is a table with a
    unique non-empty title and a non-empty notes field. The structure is
    flat by contract, so the parser reads entries directly from the table
    and rejects anything that is not a title/notes pair.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[vault_structure] section is missing or not a table")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ConfigError(
            "[vault_structure] entries must be a non-empty array of tables"
        )
    entries: list[VaultEntry] = []
    seen_titles: set[str] = set()
    for entry_raw in entries_raw:
        if not isinstance(entry_raw, dict):
            raise ConfigError("[vault_structure] entries must be tables")
        title = entry_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ConfigError(
                "[vault_structure] entry title must be a non-empty string"
            )
        if title in seen_titles:
            raise ConfigError(f"[vault_structure] duplicate entry title: {title}")
        seen_titles.add(title)
        notes = entry_raw.get("notes")
        if not isinstance(notes, str) or not notes:
            raise ConfigError(
                f"[vault_structure] entry {title}: notes must be a non-empty string"
            )
        entries.append(VaultEntry(title=title, notes=notes))
    return VaultStructureConfig(entries=tuple(entries))


def _local_vault_setup_table(raw: object) -> LocalVaultSetupConfig:
    """Validate the [local_vault_setup] table and build the config.

    Source vault paths and the entry title are non-empty strings; the
    source paths are repository-root relative, the target paths absolute
    (the fixed locations from docs/spec/secrets-model.md).
    """

    if not isinstance(raw, dict):
        raise ConfigError("[local_vault_setup] section is missing or not a table")
    source_vault_production = raw.get("source_vault_production")
    if not isinstance(source_vault_production, str) or not source_vault_production:
        raise ConfigError(
            "local_vault_setup.source_vault_production must be a non-empty string"
        )
    source_vault_default = raw.get("source_vault_default")
    if not isinstance(source_vault_default, str) or not source_vault_default:
        raise ConfigError(
            "local_vault_setup.source_vault_default must be a non-empty string"
        )
    local_vault_path = raw.get("local_vault_path")
    if not isinstance(local_vault_path, str) or not local_vault_path:
        raise ConfigError(
            "local_vault_setup.local_vault_path must be a non-empty string"
        )
    pass_file_path = raw.get("pass_file_path")
    if not isinstance(pass_file_path, str) or not pass_file_path:
        raise ConfigError(
            "local_vault_setup.pass_file_path must be a non-empty string"
        )
    vault_password_entry_title = raw.get("vault_password_entry_title")
    if not isinstance(vault_password_entry_title, str) or not vault_password_entry_title:
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must be a non-empty string"
        )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"local_vault_setup.{name}")

    error_priority = _int_field(
        raw.get("error_priority"), "local_vault_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "local_vault_setup.error_priority must be between 0 and 7"
        )
    return LocalVaultSetupConfig(
        source_vault_production=Path(source_vault_production),
        source_vault_default=Path(source_vault_default),
        local_vault_path=Path(local_vault_path),
        pass_file_path=Path(pass_file_path),
        vault_password_entry_title=vault_password_entry_title,
        secrets_dir_mode=_file_mode_field("secrets_dir_mode"),
        local_vault_file_mode=_file_mode_field("local_vault_file_mode"),
        pass_dir_mode=_file_mode_field("pass_dir_mode"),
        pass_file_mode=_file_mode_field("pass_file_mode"),
        error_priority=error_priority,
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
    vault_structure = _vault_structure_table(data.get("vault_structure"))
    local_vault_setup = _local_vault_setup_table(data.get("local_vault_setup"))
    if not any(
        entry.title == local_vault_setup.vault_password_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must name an entry "
            "of the [vault_structure] table"
        )
    return Config(
        engine=_engine_table(data.get("engine")),
        cli_tools=_cli_tools_table(data.get("cli_tools")),
        add_extra_repos=_add_extra_repos_table(data.get("add_extra_repos")),
        swapfile_service_install=_swapfile_service_install_table(
            data.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(data.get("zswap_service")),
        zram_service=_zram_service_table(data.get("zram_service")),
        system_metrics_setup=_system_metrics_setup_table(
            data.get("system_metrics_setup")
        ),
        vault_structure=vault_structure,
        local_vault_setup=local_vault_setup,
        tasks=_tasks_table(data.get("tasks")),
    )
