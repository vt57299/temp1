from __future__ import annotations

import hashlib
import re
from pathlib import Path


def compute_sha256_from_bytes(data: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(data)
    return hasher.hexdigest()


def compute_sha256_from_file(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    return cleaned or "file"
