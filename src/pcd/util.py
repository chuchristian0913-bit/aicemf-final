"""Small, boring helpers. Everything that touches bytes goes through here so that
hashes are reproducible across platforms.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- paths


def repo_root() -> Path:
    """Repository root, resolved from this file's location (src/pcd/util.py)."""
    return Path(__file__).resolve().parents[2]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel(path: Path) -> str:
    """Repository-relative POSIX path, for stable evidence records."""
    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------- time


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------- json


def canon(obj: Any) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace, UTF-8 text.

    Hashing this string is what makes 'the same artifact' a testable claim.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_obj(obj: Any) -> str:
    return sha256_text(canon(obj))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- io


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_text(path: Path, text: str) -> Path:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def write_json(path: Path, obj: Any, indent: int = 2) -> Path:
    return write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record: dict) -> None:
    ensure_dir(path.parent)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(canon(record) + "\n")


def load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# ------------------------------------------------------------------------- numbers


def safe_div(num: float | None, den: float | None) -> float | None:
    """Division that refuses to invent a number.

    A zero denominator returns None, which callers must render as not_available.
    This is the single most important numeric helper in the project: silent zeros
    are how research notes end up with a 0.1% net margin.
    """
    if num is None or den is None or den == 0:
        return None
    return num / den


def pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def usd_b(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x / 1e9:,.{digits}f}"


def round_sig(x: float | None, digits: int = 6) -> float | None:
    """Round for storage, not for display. Keeps recomputation stable."""
    if x is None:
        return None
    return float(f"{x:.{digits}g}")


def chunk(items: Iterable, size: int) -> Iterable[list]:
    buf: list = []
    for it in items:
        buf.append(it)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
