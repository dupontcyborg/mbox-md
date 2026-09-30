"""Write the output tree: message files, `messages.jsonl`, and `attachments/index.jsonl`."""

import collections
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .attachments import StoredAttachment


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
    path: str | None = None
    size: int = 0
    names: collections.Counter[str] = field(default_factory=collections.Counter)
    messages: list[str] = field(default_factory=list)


class AttachmentIndex:
    """Collects which messages reference each attachment hash, then writes `attachments/index.jsonl`."""

    def __init__(self) -> None:
        self.entries: dict[str, _IndexEntry] = collections.defaultdict(_IndexEntry)

    def add(self, message_path: str, attachments: list[StoredAttachment]) -> None:
        for at in attachments:
            e = self.entries[at.hash]
            e.path, e.size = at.path, at.size
            e.names[at.name] += 1
            e.messages.append(message_path)

    @property
    def unique_bytes(self) -> int:
        return sum(e.size for e in self.entries.values())

    @property
    def referenced_bytes(self) -> int:
        return sum(e.size * len(e.messages) for e in self.entries.values())

    def write(self, outdir: Path) -> None:
        target = outdir / "attachments" / "index.jsonl" if (outdir / "attachments").exists() else os.devnull
        with open(target, "w") as f:
            for h, e in self.entries.items():
                row = {"hash": h, "path": e.path, "size": e.size, "names": dict(e.names), "messages": e.messages}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
