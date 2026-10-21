"""Local encrypted document storage.

Uploaded files are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) before being written
under FINV_DATA_DIR/documents. The key comes from FINV_STORAGE_KEY, or is generated once into
FINV_DATA_DIR/storage.key with 0600 permissions. Anyone with the key file and the data directory can
decrypt; protect the machine accordingly (full-disk encryption is still recommended).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings


def _key() -> bytes:
    if settings.storage_key:
        return settings.storage_key.encode()
    path = settings.data_dir / "storage.key"
    if not path.exists():
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return path.read_bytes().strip()


def save(company_id: int, data: bytes) -> str:
    d = settings.upload_dir / str(company_id)
    d.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ".bin"
    p = d / name
    p.write_bytes(Fernet(_key()).encrypt(data))
    os.chmod(p, 0o600)
    return str(p.relative_to(settings.data_dir))


def load(rel_path: str) -> bytes:
    p = _resolve(rel_path)
    try:
        return Fernet(_key()).decrypt(p.read_bytes())
    except InvalidToken as exc:
        raise RuntimeError("Stored document could not be decrypted with the current storage key.") from exc


def secure_delete(rel_path: str | None) -> bool:
    """Overwrite then unlink. On SSDs/copy-on-write filesystems overwriting is best-effort; the file was
    encrypted at rest, so removing it (and, if required, rotating the key) is the effective control."""
    if not rel_path:
        return False
    p = _resolve(rel_path)
    if not p.exists():
        return False
    size = p.stat().st_size
    with open(p, "r+b") as f:
        f.write(secrets.token_bytes(size))
        f.flush()
        os.fsync(f.fileno())
    p.unlink()
    return True


def _resolve(rel_path: str) -> Path:
    base = settings.data_dir.resolve()
    p = (base / rel_path).resolve()
    if base not in p.parents:
        raise ValueError("Invalid storage path")
    return p
