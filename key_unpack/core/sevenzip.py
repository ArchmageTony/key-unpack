from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, KeyUnpackError
from .events import CancelToken


@dataclass(frozen=True)
class SevenZipResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    canceled: bool = False

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


class SevenZipRunner:
    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> None:
        self.executable = self._resolve_executable(executable)
        self.timeout_seconds = timeout_seconds

    def test_archive(
        self,
        archive: str | os.PathLike[str],
        *,
        password: str | None,
        cancel_token: CancelToken | None = None,
    ) -> SevenZipResult:
        args = [self.executable, "t", str(archive)]
        input_text = None
        if password is not None:
            if _password_requires_stdin(password):
                input_text = f"{password}\n"
            else:
                args.append(f"-p{password}")
        return self._run(args, cancel_token=cancel_token, input_text=input_text)

    def extract_archive(
        self,
        archive: str | os.PathLike[str],
        output_dir: str | os.PathLike[str],
        *,
        password: str | None,
        cancel_token: CancelToken | None = None,
    ) -> SevenZipResult:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        args = [self.executable, "x", str(archive), f"-o{output_dir}", "-y"]
        input_text = None
        if password is not None:
            if _password_requires_stdin(password):
                input_text = f"{password}\n"
            else:
                args.append(f"-p{password}")
        return self._run(args, cancel_token=cancel_token, input_text=input_text)

    def extract_stego(
        self,
        video_file: str | os.PathLike[str],
        output_dir: str | os.PathLike[str],
        *,
        cancel_token: CancelToken | None = None,
    ) -> SevenZipResult:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        args = [self.executable, "x", "-t#", str(video_file), f"-o{output_dir}", "-y"]
        return self._run(args, cancel_token=cancel_token)

    def _run(
        self,
        args: list[str],
        *,
        cancel_token: CancelToken | None,
        input_text: str | None = None,
    ) -> SevenZipResult:
        start = time.monotonic()
        creationflags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
        try:
            process = subprocess.Popen(
                args,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=start_new_session,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise KeyUnpackError(ErrorCode.SEVENZIP_MISSING, f"7-Zip executable not found: {args[0]}") from exc
        except PermissionError as exc:
            raise KeyUnpackError(ErrorCode.PERMISSION_DENIED, f"Cannot execute 7-Zip: {args[0]}") from exc

        if input_text is not None and process.stdin is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
            process.stdin = None

        try:
            while True:
                if cancel_token is not None and cancel_token.canceled:
                    self._terminate(process)
                    stdout, stderr = process.communicate()
                    return SevenZipResult(args, process.returncode or -15, stdout, stderr, canceled=True)
                if self.timeout_seconds and time.monotonic() - start > self.timeout_seconds:
                    self._terminate(process)
                    stdout, stderr = process.communicate()
                    return SevenZipResult(args, process.returncode or -9, stdout, stderr, timed_out=True)
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    return SevenZipResult(args, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        except KeyboardInterrupt:
            self._terminate(process)
            raise KeyUnpackError(ErrorCode.CANCELED, "Canceled by user") from None

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if process.poll() is not None:
                return
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    @staticmethod
    def _resolve_executable(executable: str | os.PathLike[str] | None) -> str:
        if executable:
            path = Path(executable).expanduser()
            if path.exists():
                return str(path)
            found = shutil.which(str(executable))
            if found:
                return found
            raise KeyUnpackError(ErrorCode.SEVENZIP_MISSING, f"7-Zip executable not found: {executable}")
        for name in ("7z", "7zz", "7za"):
            found = shutil.which(name)
            if found:
                return found
        raise KeyUnpackError(ErrorCode.SEVENZIP_MISSING, "7-Zip executable not found")


def _password_requires_stdin(password: str) -> bool:
    return '"' in password


def classify_result(result: SevenZipResult) -> ErrorCode | None:
    if result.returncode == 0:
        return None
    if result.canceled:
        return ErrorCode.CANCELED
    text = result.output.lower()
    if result.timed_out:
        return ErrorCode.UNKNOWN_ERROR
    if "enter password" in text and "break signaled" in text:
        return ErrorCode.BAD_PASSWORD
    if "wrong password" in text or ("encrypted" in text and "password" in text):
        return ErrorCode.BAD_PASSWORD
    if "missing volume" in text or "cannot find volume" in text:
        return ErrorCode.VOLUME_MISSING
    if "permission denied" in text or "access is denied" in text:
        return ErrorCode.PERMISSION_DENIED
    if "unsupported method" in text or "unsupported" in text:
        return ErrorCode.UNSUPPORTED_FORMAT
    if "cannot open the file as archive" in text or "can't open as archive" in text:
        return ErrorCode.UNSUPPORTED_FORMAT
    if "is not archive" in text or "not archive" in text:
        return ErrorCode.UNSUPPORTED_FORMAT
    if "headers error" in text or "unexpected end" in text:
        return ErrorCode.ARCHIVE_CORRUPT
    if "data error" in text or "crc failed" in text:
        return ErrorCode.ARCHIVE_CORRUPT
    return ErrorCode.UNKNOWN_ERROR
