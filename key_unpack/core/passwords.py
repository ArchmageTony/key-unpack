from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .config import DataPaths

PASSWORD_TYPES = ("one_time", "permanent", "temporary")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _expires_at_for_type(password_type: str, temporary_days: int) -> str | None:
    if password_type != "temporary":
        return None
    return (
        datetime.now(timezone.utc).astimezone() + timedelta(days=temporary_days)
    ).isoformat(timespec="seconds")


@dataclass
class PasswordRecord:
    password: str
    type: str
    created_at: str
    expires_at: str | None
    source: str
    last_used_at: str | None = None
    id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PasswordRecord":
        return cls(
            password=str(data.get("password", "")),
            type=str(data.get("type", "one_time")),
            created_at=str(data.get("created_at") or now_iso()),
            expires_at=data.get("expires_at") if isinstance(data.get("expires_at"), str) else None,
            source=str(data.get("source", "manual")),
            last_used_at=data.get("last_used_at") if isinstance(data.get("last_used_at"), str) else None,
            id=str(data.get("id") or uuid.uuid4().hex),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def expired(self) -> bool:
        expires = _parse_dt(self.expires_at)
        return expires is not None and expires <= datetime.now(timezone.utc).astimezone()


@dataclass(frozen=True)
class PasswordCandidate:
    password: str | None
    source: str
    original_password: str | None = None
    record_id: str | None = None
    password_type: str | None = None
    used_encoding_variant: bool = False
    encoding_variant_name: str | None = None

    @property
    def label(self) -> str:
        if self.password is None:
            return "no password"
        if self.used_encoding_variant:
            return f"{self.source}:{self.encoding_variant_name}"
        return self.source


class PasswordStore:
    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths
        self.path = paths.passwords_path

    def load(self, *, include_expired: bool = True) -> list[PasswordRecord]:
        records: list[PasswordRecord] = []
        if not self.path.exists():
            self.path.touch()
            return records
        with self.path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                data = json.loads(stripped)
                if not isinstance(data, dict):
                    continue
                record = PasswordRecord.from_dict(data)
                if record.password == "":
                    continue
                if include_expired or not record.expired:
                    records.append(record)
        return records

    def active_records(self) -> list[PasswordRecord]:
        return self.load(include_expired=False)

    def add_passwords(
        self,
        passwords: Iterable[str],
        *,
        password_type: str,
        source: str,
        strip: bool,
        temporary_days: int,
    ) -> int:
        if password_type not in PASSWORD_TYPES:
            raise ValueError(f"Invalid password type: {password_type}")
        records = self.load()
        existing = {record.password: record for record in records}
        created_at = now_iso()

        changed = 0
        seen_in_import: set[str] = set()
        for raw in passwords:
            password = raw.strip() if strip else raw.rstrip("\r\n")
            if not password or password in seen_in_import:
                continue
            seen_in_import.add(password)
            expires_at = _expires_at_for_type(password_type, temporary_days)
            current = existing.get(password)
            if current is not None:
                if (
                    current.type != password_type
                    or current.expires_at != expires_at
                    or current.source != source
                ):
                    current.type = password_type
                    current.expires_at = expires_at
                    current.source = source
                    changed += 1
                continue
            records.append(
                PasswordRecord(
                    id=uuid.uuid4().hex,
                    password=password,
                    type=password_type,
                    created_at=created_at,
                    expires_at=expires_at,
                    source=source,
                    last_used_at=None,
                )
            )
            existing[password] = records[-1]
            changed += 1
        if changed:
            self._write_records(records, backup=True)
        return changed

    def import_file(
        self,
        file_path: str | os.PathLike[str],
        *,
        password_type: str,
        strip: bool,
        temporary_days: int,
    ) -> int:
        with Path(file_path).open("r", encoding="utf-8-sig") as handle:
            return self.add_passwords(
                handle,
                password_type=password_type,
                source=f"import:{Path(file_path).name}",
                strip=strip,
                temporary_days=temporary_days,
            )

    def cleanup_expired(self) -> int:
        records = self.load(include_expired=True)
        kept = [record for record in records if not record.expired]
        removed = len(records) - len(kept)
        if removed:
            self._write_records(kept, backup=True)
        return removed

    def update_record(
        self,
        record_id: str,
        *,
        password: str,
        password_type: str,
        temporary_days: int,
    ) -> bool:
        if password_type not in PASSWORD_TYPES:
            raise ValueError(f"Invalid password type: {password_type}")
        records = self.load(include_expired=True)
        if any(record.id != record_id and record.password == password for record in records):
            raise ValueError("Password already exists")
        expires_at = _expires_at_for_type(password_type, temporary_days)
        changed = False
        for record in records:
            if record.id == record_id:
                if (
                    record.password == password
                    and record.type == password_type
                    and record.expires_at == expires_at
                ):
                    break
                record.password = password
                record.type = password_type
                record.expires_at = expires_at
                changed = True
                break
        if changed:
            self._write_records(records, backup=True)
        return changed

    def delete_records(self, record_ids: Iterable[str]) -> int:
        ids = set(record_ids)
        if not ids:
            return 0
        records = self.load(include_expired=True)
        kept = [record for record in records if record.id not in ids]
        removed = len(records) - len(kept)
        if removed:
            self._write_records(kept, backup=True)
        return removed

    def mark_success(self, candidate: PasswordCandidate) -> bool:
        if candidate.original_password is None:
            return False
        records = self.load(include_expired=True)
        changed = False
        kept: list[PasswordRecord] = []
        used_at = now_iso()
        for record in records:
            is_match = record.id == candidate.record_id or record.password == candidate.original_password
            if is_match and record.type == "one_time":
                changed = True
                continue
            if is_match:
                record.last_used_at = used_at
                changed = True
            kept.append(record)
        if changed:
            self._write_records(kept, backup=True)
        return changed

    def _write_records(self, records: list[PasswordRecord], *, backup: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backup:
            self._backup_current()
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
            os.replace(tmp_name, self.path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _backup_current(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        self.paths.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d%H%M%S")
        target = self.paths.backups_dir / f"passwords-{stamp}.jsonl"
        shutil.copy2(self.path, target)
        backups = sorted(self.paths.backups_dir.glob("passwords-*.jsonl"))
        for old in backups[:-5]:
            old.unlink(missing_ok=True)


def _encoding_variants(password: str) -> list[tuple[str, str]]:
    if password.isascii():
        return []
    variants: list[tuple[str, str]] = []
    try:
        converted = password.encode("utf-8").decode("gbk")
    except UnicodeDecodeError:
        converted = ""
    if converted and converted != password:
        variants.append(("utf8_bytes_as_gbk", converted))
    return variants


def build_candidates(
    records: Iterable[PasswordRecord],
    *,
    enable_encoding_compat: bool,
) -> list[PasswordCandidate]:
    candidates = [PasswordCandidate(password=None, source="none")]
    by_type = {"one_time": [], "permanent": [], "temporary": []}
    for record in records:
        if record.type in by_type and not record.expired:
            by_type[record.type].append(record)
    for password_type in ("one_time", "permanent", "temporary"):
        for record in by_type[password_type]:
            candidates.append(
                PasswordCandidate(
                    password=record.password,
                    source=record.type,
                    original_password=record.password,
                    record_id=record.id,
                    password_type=record.type,
                )
            )
            if enable_encoding_compat:
                for variant_name, variant in _encoding_variants(record.password):
                    candidates.append(
                        PasswordCandidate(
                            password=variant,
                            source=record.type,
                            original_password=record.password,
                            record_id=record.id,
                            password_type=record.type,
                            used_encoding_variant=True,
                            encoding_variant_name=variant_name,
                        )
                    )
    return candidates
