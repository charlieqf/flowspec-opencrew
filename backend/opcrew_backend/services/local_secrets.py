from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import json
import os
import platform
import secrets
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


KEYCHAIN_SERVICE = "OpenCrew Local Secret Store"
KEYCHAIN_ACCOUNT = "opencrew"


class LocalSecretStoreError(RuntimeError):
    pass


def _fernet_key_from_value(value: str) -> bytes:
    raw = value.strip().encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(raw)
        if len(decoded) == 32:
            return base64.urlsafe_b64encode(decoded)
    except Exception:
        pass
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


class LocalSecretStore:
    """Encrypted local secret file backed by a device/user scoped master key."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = Path(os.environ.get("OPENCREW_SECRET_STORE_PATH") or self.data_dir / "secrets.enc")
        self._fernet: Fernet | None = None
        self._key_source = ""
        self._lock = threading.RLock()

    @property
    def key_source(self) -> str:
        self._ensure_fernet()
        return self._key_source

    def status(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "key_source": self._key_source or "lazy",
        }

    def get(self, ref: str, default: str = "") -> str:
        ref = ref.strip()
        if not ref:
            return default
        try:
            return str(self._read_payload().get("values", {}).get(ref) or default)
        except Exception:
            return default

    def has(self, ref: str) -> bool:
        return bool(self.get(ref))

    def set(self, ref: str, value: str) -> None:
        ref = ref.strip()
        if not ref:
            raise LocalSecretStoreError("secret ref is required")
        self.set_many({ref: value})

    def set_many(self, values_to_set: dict[str, str]) -> None:
        cleaned = {str(ref).strip(): str(value or "") for ref, value in values_to_set.items() if str(ref).strip()}
        if not cleaned:
            return
        with self._lock, self._file_lock(self._store_lock_path()):
            payload = self._read_payload_unlocked()
            values = payload.setdefault("values", {})
            for ref, value in cleaned.items():
                if value:
                    values[ref] = value
                else:
                    values.pop(ref, None)
            payload["updated_at"] = int(time.time() * 1000)
            self._write_payload_unlocked(payload)

    def delete(self, ref: str) -> None:
        self.set(ref, "")

    def refs(self) -> list[str]:
        return sorted(str(key) for key in self._read_payload().get("values", {}).keys())

    def _ensure_fernet(self) -> Fernet:
        with self._lock:
            if self._fernet is not None:
                return self._fernet
            key = self._load_master_key()
            self._fernet = Fernet(key)
            return self._fernet

    def _load_master_key(self) -> bytes:
        env_key = os.environ.get("OPENCREW_SECRET_STORE_KEY", "").strip()
        if env_key:
            self._key_source = "env"
            return _fernet_key_from_value(env_key)
        if os.environ.get("OPENCREW_SECRET_STORE_KEYCHAIN", "").strip().lower() in {"1", "true", "yes", "on"} and platform.system() == "Darwin" and shutil.which("security"):
            self._key_source = "macos_keychain"
            with self._file_lock(self.data_dir / ".secret_store_master_key.lock"):
                return self._load_or_create_keychain_key()
        self._key_source = "local_key_file"
        return self._load_or_create_dev_key_file()

    def _load_or_create_keychain_key(self) -> bytes:
        existing = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if existing.returncode == 0:
            key_value = existing.stdout.strip()
            if not key_value:
                raise LocalSecretStoreError("macOS Keychain master key exists but is empty")
            return _fernet_key_from_value(key_value)
        key = Fernet.generate_key().decode("ascii")
        created = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
                key,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()
            raise LocalSecretStoreError(f"failed to create macOS Keychain master key: {detail}")
        return key.encode("ascii")

    def _load_or_create_dev_key_file(self) -> bytes:
        path = Path(os.environ.get("OPENCREW_SECRET_STORE_DEV_KEY_PATH") or self.data_dir / "secret_store.key")
        with self._file_lock(path.with_name(f".{path.name}.lock")):
            if path.exists():
                key_value = path.read_text(encoding="utf-8").strip()
                if not key_value:
                    raise LocalSecretStoreError(f"local secret master key is empty: {path}")
                return _fernet_key_from_value(key_value)
            path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key().decode("ascii")
            tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
            tmp_path.write_text(key, encoding="utf-8")
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            tmp_path.replace(path)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            return key.encode("ascii")

    def _read_payload(self) -> dict[str, Any]:
        with self._lock, self._file_lock(self._store_lock_path()):
            return self._read_payload_unlocked()

    def _read_payload_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "created_at": int(time.time() * 1000), "updated_at": int(time.time() * 1000), "values": {}}
        try:
            raw = self.path.read_bytes()
            decoded = self._ensure_fernet().decrypt(raw)
            payload = json.loads(decoded.decode("utf-8"))
            if not isinstance(payload, dict):
                raise LocalSecretStoreError("secret store payload is not an object")
            payload.setdefault("values", {})
            return payload
        except InvalidToken as exc:
            raise LocalSecretStoreError(f"cannot decrypt local secret store: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise LocalSecretStoreError(f"local secret store is corrupted: {self.path}") from exc

    def _write_payload(self, payload: dict[str, Any]) -> None:
        with self._lock, self._file_lock(self._store_lock_path()):
            self._write_payload_unlocked(payload)

    def _write_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encrypted = self._ensure_fernet().encrypt(plaintext)
        tmp_path = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        tmp_path.write_bytes(encrypted)
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        tmp_path.replace(self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def _store_lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.lock")

    @contextmanager
    def _file_lock(self, path: Path) -> Any:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
