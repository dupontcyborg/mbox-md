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


def is_known_ext(ext: str) -> bool:
    return ext in mimetypes.types_map or ext in mimetypes.common_types


def plausible_suffix(suf: str) -> bool:
    """A filename suffix worth trusting: one mimetypes knows, or a short one that starts with a letter.

    Rejects junk such as `.e3fc6c20` (hex ids after a dot) or `.8` from names like `v1.8`.
    """
    if is_known_ext(suf):
        return True
    looks_like_hex_id = len(suf) >= 5 and re.fullmatch(r"\.[0-9a-f]*[0-9][0-9a-f]*", suf) is not None
    return re.fullmatch(r"\.[a-z][a-z0-9]{0,5}", suf) is not None and not looks_like_hex_id


def ext_for(name: str, ctype: str, data: bytes = b"") -> str:
    suf = Path(name).suffix.lower()
    if suf and plausible_suffix(suf):
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

    @staticmethod
    def address(data: bytes, ext: str) -> tuple[str, Path]:
        """Where `data` is (or would be) stored, relative to the output folder. Writes nothing."""
        h = hashlib.sha256(data).hexdigest()[:20]
        return h, Path("attachments") / h[:2] / f"{h}{ext}"

    def put(self, data: bytes, ext: str) -> tuple[str, Path]:
        h, rel = self.address(data, ext)
        dest = self.outdir / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        return h, rel
