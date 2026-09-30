"""Write the output tree: message files, `messages.jsonl`, and `attachments/index.jsonl`."""

import collections
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TextIO
from urllib.parse import quote

from .attachments import StoredAttachment, is_known_ext


def write_message(outdir: Path, relpath: Path, text: str) -> None:
    path = outdir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_unparsed(outdir: Path, key: str, raw: bytes) -> str:
    d = outdir / "_unparsed"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.eml").write_bytes(raw)
    return f"_unparsed/{key}.eml"


def write_manifest_line(f: TextIO, message_id: str, path: str, subject: str) -> None:
    f.write(json.dumps({"message_id": message_id, "path": path, "subject": subject}, ensure_ascii=False) + "\n")


@dataclass
class _IndexEntry:
    size: int = 0
    names: collections.Counter[str] = field(default_factory=collections.Counter)
    refs: list[tuple[str, str]] = field(default_factory=list)
    """(message path, attachment path) per reference. Workers pick the extension, so one hash can have several."""

    @property
    def messages(self) -> list[str]:
        return [message for message, _ in self.refs]

    @property
    def paths(self) -> collections.Counter[str]:
        return collections.Counter(path for _, path in self.refs)

    @property
    def path(self) -> str:
        """The canonical file: a known extension first, then the most used one, then alphabetical."""
        counts = self.paths
        return min(counts, key=lambda p: (not is_known_ext(PurePosixPath(p).suffix), -counts[p], p))


class AttachmentIndex:
    """Collects which messages reference each attachment hash, then writes `attachments/index.jsonl`."""

    def __init__(self) -> None:
        self.entries: dict[str, _IndexEntry] = collections.defaultdict(_IndexEntry)

    def add(self, message_path: str, attachments: list[StoredAttachment]) -> None:
        for at in attachments:
            e = self.entries[at.hash]
            e.size = at.size
            e.names[at.name] += 1
            e.refs.append((message_path, at.path))

    @property
    def unique_bytes(self) -> int:
        return sum(e.size for e in self.entries.values())

    @property
    def referenced_bytes(self) -> int:
        return sum(e.size * len(e.refs) for e in self.entries.values())

    def consolidate(self, outdir: Path) -> int:
        """Keep one file per hash: point every message at the canonical file and delete the others.

        Parallel workers store the same bytes under each extension they see (`photo.jpg`, `photo.jpeg`), and
        doing this afterwards makes the result independent of which worker finished first. Returns the number
        of files removed.
        """
        removed = 0
        for e in self.entries.values():
            if len(e.paths) < 2:
                continue
            canonical = e.path
            for message, old in e.refs:
                if old != canonical:
                    md = outdir / message
                    text = md.read_text(encoding="utf-8")
                    text = _replace_path(_replace_path(text, old, canonical), quote(old), quote(canonical))
                    md.write_text(text, encoding="utf-8")
            for old in e.paths:
                if old != canonical and (outdir / old).exists():
                    (outdir / old).unlink()
                    removed += 1
            e.refs = [(message, canonical) for message, _ in e.refs]
        return removed

    def write(self, outdir: Path) -> None:
        target = outdir / "attachments" / "index.jsonl" if (outdir / "attachments").exists() else os.devnull
        with open(target, "w") as f:
            for h, e in self.entries.items():
                row = {"hash": h, "path": e.path, "size": e.size, "names": dict(e.names), "messages": e.messages}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _replace_path(text: str, old: str, new: str) -> str:
    """Replace whole paths only, so `x.htm` -> `x.html` never turns an existing `x.html` into `x.htmll`."""
    return re.sub(re.escape(old) + r"(?![\w.%-])", lambda _: new, text)
