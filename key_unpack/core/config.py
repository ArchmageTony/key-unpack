from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, KeyUnpackError


DEFAULT_CONFIG: dict[str, Any] = {
    "default_password_type": "one_time",
    "temporary_password_days": 7,
    "strip_imported_passwords": True,
    "enable_password_encoding_compat": True,
    "output_strategy": "archive_dir",
    "overwrite": "rename",
    "log_success_password": True,
    "max_log_records": 1000,
    "max_log_bytes": 1048576,
    "command_timeout_seconds": 300,
}

DEFAULT_LOCAL: dict[str, Any] = {
    "sevenzip_path": None,
    "output_dir": None,
    "temp_dir": None,
}


@dataclass(frozen=True)
class DataPaths:
    data_dir: Path
    config_path: Path
    local_path: Path
    passwords_path: Path
    extract_log_path: Path
    backups_dir: Path


def default_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path.cwd()
    return app_dir / "data"


def resolve_data_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    if data_dir is None:
        return default_data_dir()
    return Path(data_dir).expanduser().resolve()


def build_data_paths(data_dir: Path) -> DataPaths:
    return DataPaths(
        data_dir=data_dir,
        config_path=data_dir / "config.json",
        local_path=data_dir / "local.json",
        passwords_path=data_dir / "passwords.jsonl",
        extract_log_path=data_dir / "extract_log.jsonl",
        backups_dir=data_dir / "backups",
    )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _load_json(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        _atomic_write_json(path, defaults)
        return dict(defaults)
    with path.open("r", encoding="utf-8-sig") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise KeyUnpackError(ErrorCode.UNKNOWN_ERROR, f"Invalid JSON object: {path}")
    merged = dict(defaults)
    merged.update(loaded)
    if merged != loaded:
        _atomic_write_json(path, merged)
    return merged


def ensure_data_dir(data_dir: str | os.PathLike[str] | None = None) -> DataPaths:
    root = resolve_data_dir(data_dir)
    paths = build_data_paths(root)
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.backups_dir.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write-test.", dir=paths.data_dir)
        os.close(fd)
        Path(probe).unlink(missing_ok=True)
        for file_path in (paths.passwords_path, paths.extract_log_path):
            file_path.touch(exist_ok=True)
    except PermissionError as exc:
        raise KeyUnpackError(
            ErrorCode.PERMISSION_DENIED,
            f"Data directory is not writable: {paths.data_dir}",
        ) from exc
    return paths


@dataclass
class AppConfig:
    paths: DataPaths
    config: dict[str, Any]
    local: dict[str, Any]

    @classmethod
    def load(cls, data_dir: str | os.PathLike[str] | None = None) -> "AppConfig":
        paths = ensure_data_dir(data_dir)
        config = _load_json(paths.config_path, DEFAULT_CONFIG)
        local = _load_json(paths.local_path, DEFAULT_LOCAL)
        return cls(paths=paths, config=config, local=local)

    def save_config(self) -> None:
        _atomic_write_json(self.paths.config_path, self.config)

    def save_local(self) -> None:
        _atomic_write_json(self.paths.local_path, self.local)

    def set_value(self, key: str, value: Any) -> None:
        if key in DEFAULT_LOCAL:
            self.local[key] = value
            self.save_local()
            return
        if key in DEFAULT_CONFIG:
            self.config[key] = value
            self.save_config()
            return
        raise KeyUnpackError(ErrorCode.UNKNOWN_ERROR, f"Unknown config key: {key}")

    @property
    def sevenzip_path(self) -> str | None:
        value = self.local.get("sevenzip_path")
        return str(value) if value else None

    @property
    def temp_dir(self) -> str | None:
        value = self.local.get("temp_dir")
        return str(value) if value else None
