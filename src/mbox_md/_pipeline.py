"""Orchestrate a conversion: read, dedup, convert in a worker pool, and write the indexes."""

import contextlib
import functools
import hashlib
import itertools
import json
import signal
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, TextIO

from ._attachments import AttachmentStore, StoredAttachment
from ._manifest import (
    ERRORS,
    ManifestEntry,
    open_manifest,
    previous_outputs,
    prune,
    read_manifest,
    rewrite_manifest,
    set_labels,
    write_ledger,
)
from ._naming import message_relpath
from ._options import ConvertOptions
from ._parse import labels_from_head, parse_or_skip
from ._reader import MboxStream, header_block, key_from_head, message_hash, message_key
from ._render import render_markdown
from ._writer import AttachmentIndex, write_message, write_unparsed


@dataclass
class ConvertStats:
    ok: int = 0
    skipped: int = 0
    """Messages with a label in `skip_labels` (Spam and Trash by default)."""
    out_of_range: int = 0
    """Messages outside `since`/`until`, including undated ones when a range is set."""
    errors: int = 0
    body_plain: int = 0
    body_html: int = 0
    body_none: int = 0
    attachment_refs: int = 0
    duplicate_message_ids_skipped: int = 0
    unique_attachments: int = 0
    attachment_bytes_referenced: int = 0
    attachment_bytes_stored: int = 0
    markdown_bytes: int = 0
    already_converted: int = 0
    """Incremental runs: messages already in the output, skipped."""
    labels_updated: int = 0
    """Messages whose labels changed: merged from duplicate copies, or updated from a newer export."""
    stale_files: int = 0
    """Files earlier runs wrote that this run didn't (delete them with `prune`)."""
    pruned: int = 0
    bytes_read: int = 0
    """How far into the input file (on-disk bytes, so compressed for .zst) the reader has got."""
    bytes_total: int = 0
    elapsed_s: float = 0.0
    dry_run: bool = False

    @property
    def processed(self) -> int:
        return self.ok + self.skipped + self.out_of_range + self.errors + self.already_converted

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def convert_one(raw: bytes, outdir: Path, options: ConvertOptions) -> dict[str, Any]:
    """Convert one message and write its files (unless `options.dry_run`). Runs inside a worker process."""
    key = message_hash(raw)
    try:
        msg = parse_or_skip(raw, options)
        if msg == "label":
            return {"status": "skipped"}
        if msg == "date":
            return {"status": "out_of_range"}
        assert not isinstance(msg, str)
        store = AttachmentStore(outdir)
        stored = []
        for part in msg.attachments:
            h, rel = store.address(part.data, part.ext) if options.dry_run else store.put(part.data, part.ext)
            stored.append(StoredAttachment(h, rel.as_posix(), part.name, len(part.data), part.content_type))
        relpath = message_relpath(options.localize(msg.date), msg.subject, msg.key)
        text = render_markdown(msg, stored, depth=len(relpath.parts) - 1)
        if not options.dry_run:
            write_message(outdir, relpath, text)
        entry = ManifestEntry(
            key=msg.key,
            path=relpath.as_posix(),
            message_id=msg.message_id,
            date=msg.date.isoformat() if msg.date else None,
            sender=msg.from_,
            subject=msg.subject,
            labels=msg.labels,
            thread_id=msg.thread_id,
            body=msg.body_kind or "none",
            attachments=stored,
        )
        return {"status": "ok", "entry": entry, "markdown_bytes": len(text.encode("utf-8"))}
    except Exception as e:
        path = None if options.dry_run else write_unparsed(outdir, key, raw)
        mid = message_key(raw)
        return {
            "status": "error",
            "error": repr(e),
            "path": path,
            "key": key,
            "message_id": mid.decode("utf-8", "replace") if mid.startswith(b"<") else "",
        }


CHUNK = 16  # messages per task sent to a worker
IN_FLIGHT_PER_WORKER = 4  # bounds memory: at most workers * 4 * CHUNK raw messages are held at once


def convert_chunk(raws: list[bytes], outdir: Path, options: ConvertOptions) -> list[dict[str, Any]]:
    return [convert_one(raw, outdir, options) for raw in raws]


def _ignore_sigint() -> None:
    # Ctrl-C reaches the whole process group; let only the main process handle it, so an interrupt stops the run
    # cleanly instead of killing workers and surfacing as a broken pool.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def run_parallel(
    work: Callable[[list[bytes]], list[dict[str, Any]]], messages: Iterator[bytes], workers: int
) -> Iterator[dict[str, Any]]:
    """Yield results as workers finish, feeding the pool lazily so the mbox is never held in memory."""
    chunks = iter(lambda: list(itertools.islice(messages, CHUNK)), [])
    ex = ProcessPoolExecutor(workers, initializer=_ignore_sigint)
    try:
        pending: set[Future[list[dict[str, Any]]]] = set()
        for chunk in chunks:
            pending.add(ex.submit(work, chunk))
            if len(pending) >= workers * IN_FLIGHT_PER_WORKER:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    yield from f.result()
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in done:
                yield from f.result()
    except BaseException:
        # Interrupted, a worker died, or the caller stopped early: don't wait for queued work.
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown()


class WorkerError(RuntimeError):
    """A worker process failed to start or died mid-run."""


def convert(
    src: str | Path,
    outdir: str | Path,
    options: ConvertOptions | None = None,
    *,
    on_progress: Callable[[ConvertStats], None] | None = None,
    on_error: Callable[[str | None, str], None] | None = None,
) -> ConvertStats:
    """Convert the mbox at `src` into Markdown under `outdir`.

    `on_progress` is called after every message with the running stats. `on_error(path, error)` is called for each
    message that failed; `path` is its raw copy under `_unparsed/`, or None in a dry run. With `options.dry_run`,
    nothing is written and `outdir` isn't created. Raises `ReadError` if the input can't be read and `WorkerError`
    if the worker processes can't run.
    """
    options = options or ConvertOptions()
    outdir = Path(outdir)
    stream = MboxStream(src, unescape_from=options.unescape_from)
    if not options.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
    stats = ConvertStats(bytes_total=stream.total_bytes, dry_run=options.dry_run)
    previous = [] if options.dry_run else read_manifest(outdir)
    known = {e.key: e for e in previous if e.key} if options.incremental else {}
    old_outputs = set() if options.dry_run else previous_outputs(outdir, previous)
    seen: set[str] = set()
    run_labels: dict[str, list[str]] = {}  # key -> labels of every copy this run, for known keys and duplicates

    def remember_labels(key: str, labels: list[str]) -> None:
        merged = run_labels.setdefault(key, [])
        merged.extend(label for label in labels if label not in merged)

    def feed() -> Iterator[bytes]:
        with stream:
            for i, raw in enumerate(stream, 1):
                if options.limit and i > options.limit:
                    return
                stats.bytes_read = stream.position
                head = header_block(raw)
                labels = labels_from_head(head)
                if options.skip_labels & set(labels):
                    # Checked before dedup, so a Spam copy never hides the Inbox copy of the same message.
                    stats.skipped += 1
                    continue
                key = hashlib.sha1(key_from_head(head)).hexdigest()  # == message_hash(raw), without re-slicing
                if key in known:
                    stats.already_converted += 1
                    remember_labels(key, labels)
                    continue
                if key in seen:
                    stats.duplicate_message_ids_skipped += 1
                    remember_labels(key, labels)
                    continue
                seen.add(key)
                yield raw
        stats.bytes_read = stats.bytes_total

    index = AttachmentIndex()
    for e in known.values():
        index.add(e.path, e.attachments)
    entries: dict[str, ManifestEntry] = dict(known)
    if options.workers <= 1:
        results: Iterator[dict[str, Any]] = (convert_one(raw, outdir, options) for raw in feed())
    else:
        work = functools.partial(convert_chunk, outdir=outdir, options=options)
        results = run_parallel(work, feed(), options.workers)
    t0 = time.time()
    if options.dry_run:
        manifest: contextlib.AbstractContextManager[TextIO | None] = contextlib.nullcontext(None)
        errors: contextlib.AbstractContextManager[TextIO | None] = contextlib.nullcontext(None)
    else:
        manifest = open_manifest(outdir, append=options.incremental)
        (outdir / "_unparsed").mkdir(exist_ok=True)
        errors = open(outdir / ERRORS, "w", encoding="utf-8", newline="\n")
    failed: set[str] = set()
    with manifest as mf, errors as ef:
        for r in _raise_worker_errors(results):
            status = r["status"]
            if status == "ok":
                entry: ManifestEntry = r["entry"]
                stats.ok += 1
                field = f"body_{entry.body}"
                setattr(stats, field, getattr(stats, field) + 1)
                stats.attachment_refs += len(entry.attachments)
                stats.markdown_bytes += r["markdown_bytes"]
                if mf is not None:
                    mf.write(entry.to_json() + "\n")
                entries[entry.key] = entry
                index.add(entry.path, entry.attachments)
            elif status == "skipped":
                stats.skipped += 1
            elif status == "out_of_range":
                stats.out_of_range += 1
            else:
                stats.errors += 1
                if r["path"]:
                    failed.add(r["path"])
                if ef is not None:
                    row = {k: r[k] for k in ("key", "message_id", "error", "path")}
                    ef.write(json.dumps(row, ensure_ascii=False) + "\n")
                if on_error:
                    on_error(r["path"], r["error"])
            stats.elapsed_s = time.time() - t0
            if on_progress:
                on_progress(stats)

    if not options.dry_run:
        _finish_outputs(outdir, options, stats, index, entries, run_labels, set(known), old_outputs, failed)
    stats.unique_attachments = len(index.entries)
    stats.attachment_bytes_referenced = index.referenced_bytes
    stats.attachment_bytes_stored = index.unique_bytes
    stats.elapsed_s = round(time.time() - t0, 1)
    return stats


def _finish_outputs(
    outdir: Path,
    options: ConvertOptions,
    stats: ConvertStats,
    index: AttachmentIndex,
    entries: dict[str, ManifestEntry],
    run_labels: dict[str, list[str]],
    known: set[str],
    old_outputs: set[str],
    failed: set[str],
) -> None:
    """After all messages: dedup attachments, apply label changes, write the manifest and index, and find (or
    prune) files that earlier runs wrote and this one didn't."""
    index.consolidate(outdir)
    for e in entries.values():
        e.attachments = [replace(a, path=index.entries[a.hash].path) for a in e.attachments]
    for key, labels in run_labels.items():
        if (target := entries.get(key)) is None:
            continue
        # Converted earlier: this export's labels are current. New here: merge in labels of duplicate copies.
        new = labels if key in known else target.labels + [label for label in labels if label not in target.labels]
        if new != target.labels:
            set_labels(outdir, target, new)
            stats.labels_updated += 1
    rewrite_manifest(outdir, entries.values())
    index.write(outdir)
    if not failed:
        (outdir / ERRORS).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            (outdir / "_unparsed").rmdir()
    current = {e.path for e in index.entries.values()} | {e.path for e in entries.values()} | failed
    if options.incremental:
        # An incremental run doesn't see every message, so it can't tell what's stale; just record what it wrote.
        write_ledger(outdir, old_outputs | current)
        return
    stale = {p for p in old_outputs - current if (outdir / p).is_file()}
    if options.prune:
        stats.pruned = prune(outdir, stale)
        write_ledger(outdir, current)
    else:
        stats.stale_files = len(stale)
        write_ledger(outdir, stale | current)


def _raise_worker_errors(results: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    try:
        yield from results
    except BrokenProcessPool as e:
        raise WorkerError(
            "a worker process failed to start or crashed. Worker processes re-import the calling script, so "
            "call convert() from a script file under `if __name__ == '__main__':`, or pass workers=1 "
            "(for example from a REPL, stdin, or some notebooks)."
        ) from e
