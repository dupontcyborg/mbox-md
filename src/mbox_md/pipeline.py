"""Orchestrate a conversion: read, dedup, convert in a worker pool, and write the indexes."""

import functools
import multiprocessing as mp
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .attachments import AttachmentStore, StoredAttachment
from .naming import message_relpath
from .options import ConvertOptions
from .parse import parse_message
from .reader import iter_messages, message_hash, message_key
from .render import render_markdown
from .writer import AttachmentIndex, write_manifest_line, write_message, write_unparsed


@dataclass
class ConvertStats:
    ok: int = 0
    skipped: int = 0
    errors: int = 0
    body_plain: int = 0
    body_html: int = 0
    body_none: int = 0
    attachment_refs: int = 0
    duplicate_message_ids_skipped: int = 0
    unique_attachments: int = 0
    attachment_bytes_referenced: int = 0
    attachment_bytes_stored: int = 0
    elapsed_s: float = 0.0

    @property
    def processed(self) -> int:
        return self.ok + self.skipped + self.errors

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def convert_one(raw: bytes, outdir: Path, options: ConvertOptions) -> dict[str, Any]:
    """Convert one message and write its files. Runs inside a worker process."""
    try:
        msg = parse_message(raw, options)
        if msg is None:
            return {"status": "skipped"}
        store = AttachmentStore(outdir)
        stored = []
        for part in msg.attachments:
            h, rel = store.put(part.data, part.ext)
            stored.append(StoredAttachment(h, str(rel), part.name, len(part.data), part.content_type))
        relpath = message_relpath(msg.date, msg.subject, msg.key)
        write_message(outdir, relpath, render_markdown(msg, stored, depth=len(relpath.parts) - 1))
        return {
            "status": "ok",
            "message_id": msg.message_id,
            "path": str(relpath),
            "subject": msg.subject,
            "attachments": stored,
            "body_kind": msg.body_kind,
        }
    except Exception as e:
        return {"status": "error", "error": repr(e), "path": write_unparsed(outdir, message_hash(raw), raw)}


def convert(
    src: str | Path,
    outdir: str | Path,
    options: ConvertOptions | None = None,
    *,
    on_progress: Callable[[ConvertStats], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
) -> ConvertStats:
    """Convert the mbox at `src` into Markdown under `outdir`.

    `on_progress` is called after every message with the running stats. `on_error(path, error)` is
    called for each message that failed and was saved to `_unparsed/`.
    """
    options = options or ConvertOptions()
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stats = ConvertStats()
    seen: set[bytes] = set()

    def feed() -> Iterator[bytes]:
        for i, raw in enumerate(iter_messages(src), 1):
            if options.limit and i > options.limit:
                return
            k = message_key(raw)
            if k in seen:
                stats.duplicate_message_ids_skipped += 1
                continue
            seen.add(k)
            yield raw

    index = AttachmentIndex()
    work = functools.partial(convert_one, outdir=outdir, options=options)
    t0 = time.time()
    with mp.Pool(options.workers) as pool, open(outdir / "messages.jsonl", "w") as mf:
        for r in pool.imap_unordered(work, feed(), chunksize=16):
            if r["status"] == "ok":
                stats.ok += 1
                field = f"body_{r['body_kind'] or 'none'}"
                setattr(stats, field, getattr(stats, field) + 1)
                stats.attachment_refs += len(r["attachments"])
                write_manifest_line(mf, r["message_id"], r["path"], r["subject"])
                index.add(r["path"], r["attachments"])
            elif r["status"] == "skipped":
                stats.skipped += 1
            else:
                stats.errors += 1
                if on_error:
                    on_error(r["path"], r["error"])
            stats.elapsed_s = time.time() - t0
            if on_progress:
                on_progress(stats)

    index.write(outdir)
    stats.unique_attachments = len(index.entries)
    stats.attachment_bytes_referenced = index.referenced_bytes
    stats.attachment_bytes_stored = index.unique_bytes
    stats.elapsed_s = round(time.time() - t0, 1)
    return stats
