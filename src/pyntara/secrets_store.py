from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class VaultSecretsStore:
    def __init__(self, *, default_vault: Path, production_vault: Path, use_production: bool) -> None:
        self._default_vault = default_vault
        self._production_vault = production_vault
        self._use_production = use_production
        self._values: dict[str, str] = {}
        self._loaded = False

    def load(self) -> None:
        vault_path = self._production_vault if self._use_production else self._default_vault
        with vault_path.open("r", encoding="utf-8") as vault_file:
            parsed: Any = yaml.safe_load(vault_file) or {}
        if not isinstance(parsed, dict):
            raise ValueError(f"Vault file {vault_path} must contain a mapping.")
        self._values = {str(key): str(value) for key, value in parsed.items()}
        self._loaded = True

    def get(self, key: str, default: str | None = None) -> str | None:
        if not self._loaded:
            raise RuntimeError("Secrets store must be loaded before use.")
        return self._values.get(key, default)

