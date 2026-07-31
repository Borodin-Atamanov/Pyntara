from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_platform: str = "kubuntu-26.04"
    timeouts: TimeoutsConfig = TimeoutsConfig()
    passwords: PasswordConfig = PasswordConfig()
    paths: PathConfig = PathConfig()
    retry: RetryConfig = RetryConfig()
    logging: LoggingConfig = LoggingConfig()


class TaskDefinition(BaseModel):
    name: str = Field(min_length=1)
    order: int = Field(ge=0)
    description: str = Field(min_length=1)
    module: str = Field(min_length=3)
    idempotent: bool = True
    default_enabled: bool = True
    timeout_sec: int = Field(default=300, ge=1)
    depends_on: list[str] = Field(default_factory=list)
    data_subdir: str | None = None

    @model_validator(mode="after")
    def validate_dependencies(self) -> "TaskDefinition":
        unique_deps = set(self.depends_on)
        if self.name in unique_deps:
            raise ValueError("Task cannot depend on itself.")
        if len(unique_deps) != len(self.depends_on):
            raise ValueError("Task dependencies must be unique.")
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
