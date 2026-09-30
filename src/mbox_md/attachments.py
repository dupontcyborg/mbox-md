"""Content-addressed attachment storage and file extension detection."""

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAGIC = [
    (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"BM", ".bmp"),
    (b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"), (b"<!DOCTYPE html", ".html"), (b"<html", ".html"),
]  # fmt: skip


def ext_for(name: str, ctype: str, data: bytes = b"") -> str:
    suf = Path(name).suffix.lower()
    if suf and re.fullmatch(r"\.[a-z0-9]{1,8}", suf):
        return suf
    guess = mimetypes.guess_extension(ctype)
    if guess and ctype != "application/octet-stream":
        return guess
    head = data[:16].lstrip().lower()
    for magic, ext in MAGIC:
        if head.startswith(magic.lower()):
            return ext
    return guess or ".bin"


@dataclass(frozen=True)
class StoredAttachment:
    hash: str
    path: str
    """Relative to the output folder, e.g. `attachments/ab/ab12....pdf`."""
    name: str
    size: int
    type: str


class AttachmentStore:
    """Writes each attachment once under `attachments/<hh>/<sha256[:20]><ext>`. Safe across processes."""

    def __init__(self, outdir: str | Path):
        self.outdir = Path(outdir)

    def put(self, data: bytes, ext: str) -> tuple[str, Path]:
        h = hashlib.sha256(data).hexdigest()[:20]
        rel = Path("attachments") / h[:2] / f"{h}{ext}"
        dest = self.outdir / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        return h, rel
