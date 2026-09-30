"""Slugs, filenames, and folder paths for converted messages."""

import re
import unicodedata
from datetime import datetime
from pathlib import Path


def slugify(s: str | None, n: int = 60) -> str:
    s = re.sub(r"(?i)^\s*((re|fwd?|aw|wg)\s*:\s*)+", "", s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-")
    return s or "no-subject"


def safe_name(s: str | None) -> str:
    s = re.sub(r"[\x00-\x1f/\\]", "_", s or "").strip()
    return s or "attachment"


def message_relpath(date: datetime | None, subject: str, key: str) -> Path:
    """`YYYY/MM/YYYY-MM-DD-HHMM-<slug>-<id4>.md`, or `undated/undated-0000-<slug>-<id4>.md`."""
    if date is not None:
        ymd, hm = date.strftime("%Y-%m-%d"), date.strftime("%H%M")
        folder = Path(date.strftime("%Y")) / date.strftime("%m")
    else:
        ymd, hm = "undated", "0000"
        folder = Path("undated")
    return folder / f"{ymd}-{hm}-{slugify(subject)}-{key[:4]}.md"
