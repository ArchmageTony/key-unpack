from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core.config import AppConfig
from .core.errors import KeyUnpackError
from .core.events import CancelToken, TaskEvent
from .core.extract import ExtractRequest, extract_many
from .core.passwords import PASSWORD_TYPES, PasswordStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="key-unpack")
    parser.add_argument("--data-dir", help="Data directory. Defaults to ./data in source runs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="Extract one or more archives.")
    extract.add_argument("archives", nargs="+")
    extract.add_argument("--output", help="Output directory.")
    extract.add_argument(
        "--overwrite",
        choices=("skip", "overwrite", "rename", "fail"),
        default="rename",
    )
    extract.add_argument("--sevenzip", help="Custom 7-Zip executable path.")
    extract.add_argument("--temp-dir", help="Temporary directory.")
    extract.add_argument("--no-stego", action="store_true", help="Disable MP4 stego extraction.")
    extract.add_argument(
        "--no-encoding-compat",
        action="store_true",
        help="Disable Chinese password encoding compatibility candidates.",
    )
    extract.add_argument("--json", action="store_true", help="Print raw task events as JSON.")

    password = subparsers.add_parser("password", help="Manage password store.")
    password_sub = password.add_subparsers(dest="password_command", required=True)
    password_add = password_sub.add_parser("add", help="Add one password.")
    password_add.add_argument("password")
    password_add.add_argument("--type", choices=PASSWORD_TYPES, default=None)
    password_import = password_sub.add_parser("import", help="Import newline separated passwords.")
    password_import.add_argument("file")
    password_import.add_argument("--type", choices=PASSWORD_TYPES, default=None)
    password_sub.add_parser("list", help="List password records as JSONL.")
    password_sub.add_parser("cleanup", help="Remove expired temporary passwords.")

    config = subparsers.add_parser("config", help="Manage config.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_set = config_sub.add_parser("set", help="Set one config value.")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_sub.add_parser("list", help="List merged config.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            return _cmd_extract(args)
        if args.command == "password":
            return _cmd_password(args)
        if args.command == "config":
            return _cmd_config(args)
    except KeyUnpackError as exc:
        print(f"error: {exc.code.value}: {exc.message}", file=sys.stderr)
        return 2
    return 2


def _cmd_extract(args: argparse.Namespace) -> int:
    token = CancelToken()
    request = ExtractRequest(
        archives=args.archives,
        output_dir=args.output,
        overwrite=args.overwrite,
        data_dir=args.data_dir,
        enable_stego=not args.no_stego,
        enable_password_encoding_compat=False if args.no_encoding_compat else None,
        sevenzip_path=args.sevenzip,
        temp_dir=args.temp_dir,
        cancel_token=token,
    )
    failed = False
    try:
        for event in extract_many(request):
            if args.json:
                print(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")))
            else:
                print(_format_event(event))
            if event.type == "failure":
                failed = True
    except KeyboardInterrupt:
        token.cancel()
        print("canceled", file=sys.stderr)
        return 130
    return 1 if failed else 0


def _cmd_password(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.data_dir)
    store = PasswordStore(config.paths)
    password_type = getattr(args, "type", None) or str(config.config.get("default_password_type", "one_time"))
    strip = bool(config.config.get("strip_imported_passwords", True))
    temporary_days = int(config.config.get("temporary_password_days", 7))

    if args.password_command == "add":
        added = store.add_passwords(
            [args.password],
            password_type=password_type,
            source="cli",
            strip=False,
            temporary_days=temporary_days,
        )
        print(f"added {added}")
        return 0
    if args.password_command == "import":
        added = store.import_file(
            args.file,
            password_type=password_type,
            strip=strip,
            temporary_days=temporary_days,
        )
        print(f"imported {added}")
        return 0
    if args.password_command == "list":
        for record in store.load(include_expired=True):
            print(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.password_command == "cleanup":
        removed = store.cleanup_expired()
        print(f"removed {removed}")
        return 0
    return 2


def _cmd_config(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.data_dir)
    if args.config_command == "set":
        config.set_value(args.key, _coerce_config_value(args.value))
        print(f"set {args.key}")
        return 0
    if args.config_command == "list":
        data: dict[str, Any] = {}
        data.update(config.config)
        data.update(config.local)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    return 2


def _coerce_config_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _format_event(event: TaskEvent) -> str:
    current = Path(event.current_file).name if event.current_file else "-"
    progress = ""
    if event.file_index is not None and event.file_count is not None:
        progress = f"[{event.file_index}/{event.file_count}] "
    password = ""
    if event.password_index is not None and event.password_count is not None:
        password = f" password {event.password_index}/{event.password_count}"
    error = f" ({event.error_code})" if event.error_code else ""
    return f"{progress}{current}: {event.stage or event.type}{password} - {event.message}{error}"
