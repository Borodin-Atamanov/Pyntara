from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeoutsConfig(BaseModel):
    command_sec: int = Field(default=300, ge=1)
    task_sec: int = Field(default=3600, ge=1)


class PasswordConfig(BaseModel):
    root_length: int = Field(default=20, ge=8)
    user_length: int = Field(default=16, ge=8)


class PathConfig(BaseModel):
    task_data_dir: Path = Path("task_data")
    secrets_dir: Path = Path("secrets")


class RetryConfig(BaseModel):
    base_seconds: float = Field(default=1.0, gt=0)
    multiplier_mode: str = Field(default="sqrt2")


class LoggingConfig(BaseModel):
    command_output_to_console: bool = True
    command_output_to_log: bool = True
    datetime_format: str = "%Y-%m-%d-%H-%M-%S"


class UIConfig(BaseModel):
    task_pre_interaction_timeout_sec: int = Field(default=30, ge=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_platform: str = "kubuntu-26.04"
    timeouts: TimeoutsConfig = TimeoutsConfig()
    passwords: PasswordConfig = PasswordConfig()
    paths: PathConfig = PathConfig()
    retry: RetryConfig = RetryConfig()
    logging: LoggingConfig = LoggingConfig()
    ui: UIConfig = UIConfig()


class TaskDefinition(BaseModel):
    name: str = Field(min_length=1)
    order: int = Field(ge=0)
    description: str = Field(min_length=1)
    module: str = Field(min_length=3)
    idempotent: bool = True
    default_enabled: bool = True
    timeout_sec: int = Field(default=300, ge=1)
    depends_on: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    data_subdir: str | None = None
    requires_root: bool = False
    requires_network: bool = False
    requires_secrets: bool = False
    reboot_sensitive: bool = False
    state_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskDefinition:
        unique_deps = set(self.depends_on)
        if self.name in unique_deps:
            raise ValueError("Task cannot depend on itself.")
        if len(unique_deps) != len(self.depends_on):
            raise ValueError("Task dependencies must be unique.")
        unique_conflicts = set(self.conflicts_with)
        if self.name in unique_conflicts:
            raise ValueError("Task cannot conflict with itself.")
        if len(unique_conflicts) != len(self.conflicts_with):
            raise ValueError("Task conflicts must be unique.")
        if unique_deps.intersection(unique_conflicts):
            raise ValueError("Task dependency cannot be listed as a conflict.")
        return self


class InstallModesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimal: list[str] = Field(default_factory=list)
    server: list[str] = Field(default_factory=list)
    desktop: list[str] = Field(default_factory=list)
    default_desktop_mode: str = "desktop"
    default_server_mode: str = "server"
    auto_select_timeout_sec: int = Field(default=11, ge=1)


@dataclass(frozen=True, slots=True)
class TaskResult:
    success: bool
    changed: bool
    message: str | None = None
    error: str | None = None


TaskStateStatus = Literal["pending", "running", "done", "failed", "skipped"]
