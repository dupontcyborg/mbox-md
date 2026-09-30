"""`messages.jsonl`: one line per converted message, complete enough to rebuild the attachment index from it, so
incremental runs can resume and later runs can tell which files earlier runs wrote."""

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO

from ._attachments import StoredAttachment

MANIFEST = "messages.jsonl"
ERRORS = "_unparsed/errors.jsonl"
LEDGER = ".mbox-md-files"
"""Every file mbox-md has written into the folder and not deleted, across runs. Only these are ever pruned, and a
run without prune keeps listing its stale files so a later prune still finds them."""


@dataclass
class ManifestEntry:
    key: str
    """Hex id derived from the Message-ID (or the headers when there is none); stable across runs."""
    path: str
    message_id: str = ""
    date: str | None = None
    sender: str = ""
    subject: str = ""
    labels: list[str] = field(default_factory=list)
    thread_id: str | None = None
    body: str = "none"
    attachments: list[StoredAttachment] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        d["from"] = d.pop("sender")
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "ManifestEntry":
        d: dict[str, Any] = json.loads(line)
        return cls(
            key=d.get("key") or "",  # manifests from before 0.1 have no key; such entries can't be skipped
            path=d["path"],
            message_id=d.get("message_id", ""),
            date=d.get("date"),
            sender=d.get("from", ""),
            subject=d.get("subject", ""),
            labels=list(d.get("labels", [])),
            thread_id=d.get("thread_id"),
            body=d.get("body", "none"),
            attachments=[StoredAttachment(**a) for a in d.get("attachments", [])],
        )


def read_manifest(outdir: Path) -> list[ManifestEntry]:
    """Every complete line of `messages.jsonl`. A half-written last line (a killed run) is ignored."""
    path = outdir / MANIFEST
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.endswith("\n"):
                break
            try:
                entries.append(ManifestEntry.from_json(line))
            except (ValueError, KeyError, TypeError):
                continue
    return entries


def open_manifest(outdir: Path, *, append: bool) -> TextIO:
    """Open `messages.jsonl` for writing. When appending, drop a half-written last line first."""
    path = outdir / MANIFEST
    if append and path.exists():
        with open(path, "rb+") as f:
            data = f.read()
            if data and not data.endswith(b"\n"):
                f.truncate(data.rfind(b"\n") + 1)
    return open(path, "a" if append else "w", encoding="utf-8", newline="\n")


def rewrite_manifest(outdir: Path, entries: Iterable[ManifestEntry]) -> None:
    path = outdir / MANIFEST
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for e in entries:
            f.write(e.to_json() + "\n")
    os.replace(tmp, path)


_LABELS_LINE = re.compile(r"^labels: .*$", re.MULTILINE)


def set_labels(outdir: Path, entry: ManifestEntry, labels: list[str]) -> None:
    """Update the `labels:` front matter line of an already-written message file."""
    md = outdir / entry.path
    if not md.exists():
        return
    text = md.read_text(encoding="utf-8")
    front, sep, rest = text.partition("\n---\n")
    front = _LABELS_LINE.sub(lambda _: "labels: " + json.dumps(labels, ensure_ascii=False), front, count=1)
    md.write_text(front + sep + rest, encoding="utf-8", newline="\n")
    entry.labels = labels


def read_ledger(outdir: Path) -> set[str]:
    path = outdir / LEDGER
    return set(path.read_text(encoding="utf-8").splitlines()) - {""} if path.exists() else set()


def write_ledger(outdir: Path, paths: set[str]) -> None:
    path = outdir / LEDGER
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("".join(p + "\n" for p in sorted(paths)), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def previous_outputs(outdir: Path, entries: list[ManifestEntry]) -> set[str]:
    """Paths (relative to outdir) that earlier runs recorded writing: the ledger, plus what the previous manifest,
    attachment index, and `_unparsed/` list (for folders written before the ledger existed)."""
    paths = read_ledger(outdir) | {e.path for e in entries}
    for e in entries:
        paths.update(a.path for a in e.attachments)
    index = outdir / "attachments" / "index.jsonl"
    if index.exists():
        with open(index, encoding="utf-8") as f:
            for line in f:
                try:
                    paths.add(json.loads(line)["path"])
                except (ValueError, KeyError):
                    continue
    unparsed = outdir / "_unparsed"
    if unparsed.is_dir():
        paths.update(f"_unparsed/{p.name}" for p in unparsed.glob("*.eml"))
    return paths


def prune(outdir: Path, stale: set[str]) -> int:
    """Delete stale files, then any folders left empty. Returns how many files were deleted."""
    removed = 0
    root = outdir.resolve()
    for rel in sorted(stale):
        p = outdir / rel
        # Paths come from files on disk; never follow one outside the output folder (e.g. "../../x").
        if not p.resolve().is_relative_to(root) or p.resolve() == root:
            continue
        if p.is_file():
            p.unlink()
            removed += 1
            for parent in p.parents:
                if parent == outdir or not parent.is_relative_to(outdir):
                    break
                try:
                    parent.rmdir()  # only succeeds when empty
                except OSError:
                    break
    return removed
