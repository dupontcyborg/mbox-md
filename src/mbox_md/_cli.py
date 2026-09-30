"""Command-line interface. A thin layer over `mbox_md.convert`: parse flags, show progress, print a summary."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from ._compression import CompressionError, CompressResult, compress_mbox, detect
from ._options import ConvertOptions, default_workers, resolve_tz
from ._pipeline import ConvertStats, WorkerError, convert
from ._reader import ReadError

if TYPE_CHECKING:
    from rich.console import Console
    from rich.progress import TaskID

_SIZE = re.compile(r"(?i)^\s*(\d+(?:\.\d+)?)\s*(b|k|kb|kib|m|mb|mib)?\s*$")
_UNITS = {None: 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20}


def parse_size(text: str) -> int:
    """`5KB`, `5k`, `1.5 MB`, `0` -> bytes (binary units)."""
    m = _SIZE.match(text)
    if not m:
        raise argparse.ArgumentTypeError(f"not a size: {text!r} (try 5KB, 1MB, or 0)")
    return int(float(m.group(1)) * _UNITS[m.group(2).lower() if m.group(2) else None])


def parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date: {text!r} (use YYYY-MM-DD)") from None


def parse_labels(text: str) -> frozenset[str]:
    return frozenset(label.strip() for label in text.split(",") if label.strip())


def human_bytes(n: float) -> str:
    """Decimal units, matching rich's progress bar and most file browsers."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1000
    raise AssertionError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mbox-md",
        description="Convert an mbox archive (such as a Gmail Takeout export) into one Markdown file per message.",
        epilog="Example: mbox-md 'All mail Including Spam and Trash.mbox' archive/ --since 2020-01-01",
    )
    ap.add_argument("mbox", type=Path, help="input .mbox, .mbox.zst, or .mbox.gz")
    ap.add_argument("outdir", type=Path, nargs="?", help="output folder (not needed with --dry-run)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sel = ap.add_argument_group("which messages")
    sel.add_argument("--since", type=parse_day, metavar="YYYY-MM-DD", help="only messages on or after this day")
    sel.add_argument("--until", type=parse_day, metavar="YYYY-MM-DD", help="only messages on or before this day")
    sel.add_argument(
        "--skip-labels",
        type=parse_labels,
        default=ConvertOptions().skip_labels,
        metavar="LABELS",
        help='comma-separated Gmail labels to skip (default: Spam,Trash; "" keeps everything)',
    )
    sel.add_argument("--limit", type=int, metavar="N", help="stop after reading N messages")
    sel.add_argument(
        "--tz",
        default="sender",
        metavar="ZONE",
        help="timezone for folder/file names and --since/--until: sender (default: each message's own), utc, "
        "local, or an IANA name like Europe/Paris",
    )

    content = ap.add_argument_group("content")
    content.add_argument(
        "--min-inline-image",
        type=parse_size,
        default=ConvertOptions().min_inline_image,
        metavar="SIZE",
        help="drop inline images smaller than this, e.g. logos and tracking pixels (default: 5KB; 0 keeps all)",
    )
    content.add_argument("--strip-quotes", action="store_true", help="drop quoted replies from message bodies")
    content.add_argument(
        "--keep-from-escapes",
        action="store_true",
        help="don't undo mboxrd '>From ' escaping (use for mboxo files, where '>From ' is literal text)",
    )

    run = ap.add_argument_group("running")
    run.add_argument("--workers", type=int, default=default_workers(), help="worker processes (default: CPUs - 1)")
    run.add_argument("--dry-run", action="store_true", help="count messages and estimate output size; write nothing")
    reruns = run.add_mutually_exclusive_group()
    reruns.add_argument(
        "--incremental",
        action="store_true",
        help="only convert messages not already in OUTDIR (new mail from a newer export, or resuming a stopped run)",
    )
    reruns.add_argument(
        "--prune", action="store_true", help="delete files that earlier runs wrote into OUTDIR and this run didn't"
    )

    keep = ap.add_argument_group("compressing the source mbox (after a successful conversion)")
    keep.add_argument(
        "--compress-source",
        choices=["zstd", "gzip"],
        help="compress the input mbox next to it (.zst or .gz), verified by a full decompression",
    )
    keep.add_argument(
        "--compress-level", type=int, default=5, metavar="1-10", help="1 fastest to 10 smallest (default: 5)"
    )
    keep.add_argument(
        "--delete-source", action="store_true", help="delete the original mbox, only after the compressed copy verifies"
    )

    out = ap.add_argument_group("output")
    mode = out.add_mutually_exclusive_group()
    mode.add_argument("-q", "--quiet", action="store_true", help="no progress or summary; only errors")
    mode.add_argument("-v", "--verbose", action="store_true", help="also report each message that fails")
    mode.add_argument("--json", action="store_true", help="print the stats as JSON on stdout, nothing else")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.outdir is None and not a.dry_run:
        ap.error("outdir is required (or use --dry-run)")
    if a.since and a.until and a.since > a.until:
        ap.error("--since is after --until")
    if a.workers < 1:
        ap.error("--workers must be at least 1")
    if a.delete_source and not a.compress_source:
        ap.error("--delete-source needs --compress-source")
    if a.compress_source and a.dry_run:
        ap.error("--compress-source can't be combined with --dry-run")
    if not 1 <= a.compress_level <= 10:
        ap.error("--compress-level must be 1 to 10")
    if (a.incremental or a.prune) and a.dry_run:
        ap.error("--incremental and --prune can't be combined with --dry-run")
    try:
        resolve_tz(a.tz)
    except ValueError as e:
        ap.error(str(e))
    if a.compress_source and a.mbox.is_file() and detect(a.mbox) is not None:
        ap.error(f"{a.mbox} is already compressed")

    from rich.console import Console

    err = Console(stderr=True, highlight=False)
    options = ConvertOptions(
        skip_labels=a.skip_labels,
        min_inline_image=a.min_inline_image,
        since=a.since,
        until=a.until,
        strip_quotes=a.strip_quotes,
        tz=a.tz,
        unescape_from=not a.keep_from_escapes,
        incremental=a.incremental,
        prune=a.prune,
        dry_run=a.dry_run,
        workers=a.workers,
        limit=a.limit,
    )
    outdir = a.outdir or Path(".")
    failures: list[tuple[str | None, str]] = []

    def on_error(path: str | None, error: str) -> None:
        failures.append((path, error))
        if a.verbose:
            err.print(f"[yellow]failed[/] {path or '(dry run)'}: {error}")

    try:
        if a.quiet or a.json:
            stats = convert(a.mbox, outdir, options, on_error=on_error)
        else:
            stats = _convert_with_progress(a.mbox, outdir, options, err, on_error)
    except (ReadError, CompressionError) as e:
        err.print(f"[red]error:[/] {e}")
        return 1
    except WorkerError as e:
        err.print(f"[red]error:[/] {e}")
        return 1
    except KeyboardInterrupt:
        where = "" if a.dry_run else f"; partial output is in {outdir}"
        err.print(f"[yellow]interrupted{where}[/]")
        return 130

    if a.json:
        print(json.dumps(stats.as_dict(), indent=1))
    elif not a.quiet:
        _print_summary(Console(highlight=False), stats, outdir)
    if failures and not a.verbose and not a.json:
        where = "" if a.dry_run else f"; raw copies are in {outdir / '_unparsed'}"
        err.print(f"[yellow]{len(failures):,} message(s) failed{where}. Rerun with --verbose for details.[/]")
    if a.compress_source:
        try:
            result = _compress_source(a, err, show_progress=not (a.quiet or a.json))
        except CompressionError as e:
            err.print(f"[red]error:[/] {e}")
            return 1
        except KeyboardInterrupt:
            err.print(f"[yellow]interrupted; {a.mbox} was not changed[/]")
            return 130
        if not (a.quiet or a.json):
            kept = "deleted the original" if result.source_deleted else "kept the original"
            err.print(
                f"Compressed {a.mbox.name} → {result.dest.name}: {human_bytes(result.source_bytes)} → "
                f"{human_bytes(result.compressed_bytes)} ({result.ratio:.2f}x), verified; {kept}"
            )
    return 0


def _compress_source(a: argparse.Namespace, err: Console, *, show_progress: bool) -> CompressResult:
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

    columns = [TextColumn("{task.description:<11}"), BarColumn(), DownloadColumn(), TransferSpeedColumn()]
    with Progress(*columns, TimeRemainingColumn(), console=err, disable=not (show_progress and err.is_terminal)) as p:
        tasks: dict[str, TaskID] = {}

        def on_progress(phase: str, done: int, total: int) -> None:
            if phase not in tasks:
                tasks[phase] = p.add_task("Compressing" if phase == "compress" else "Verifying", total=total)
            p.update(tasks[phase], completed=done, total=total)

        return compress_mbox(
            a.mbox,
            algorithm=a.compress_source,
            level=a.compress_level,
            delete_source=a.delete_source,
            on_progress=on_progress,
        )


def _convert_with_progress(
    src: Path, outdir: Path, options: ConvertOptions, err: Console, on_error: Callable[[str | None, str], None]
) -> ConvertStats:
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

    verb = "Scanning" if options.dry_run else "Converting"
    target = "" if options.dry_run else f" → {outdir}"
    err.print(f"{verb} [bold]{src.name}[/]{target} with {options.workers} worker(s)")
    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        DownloadColumn(),
        TextColumn("{task.fields[messages]:>9,} messages"),
        TextColumn("{task.fields[rate]:>7,.0f}/s"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
    ]
    with Progress(*columns, console=err, transient=False, disable=not err.is_terminal) as progress:
        task = progress.add_task(verb, total=None, messages=0, rate=0.0)

        def on_progress(s: ConvertStats) -> None:
            rate = s.processed / s.elapsed_s if s.elapsed_s > 0 else 0.0
            progress.update(task, total=s.bytes_total, completed=s.bytes_read, messages=s.processed, rate=rate)

        stats = convert(src, outdir, options, on_progress=on_progress, on_error=on_error)
        progress.update(task, total=stats.bytes_total, completed=stats.bytes_total, messages=stats.processed)
    return stats


def _print_summary(console: Console, s: ConvertStats, outdir: Path) -> None:
    from rich.table import Table

    title = "Dry run: nothing was written" if s.dry_run else "Done"
    t = Table(title=title, title_justify="left", show_header=False, box=None, padding=(0, 2, 0, 0))
    t.add_column(style="bold", no_wrap=True)
    t.add_column(justify="right", no_wrap=True)
    t.add_column(style="dim", overflow="fold")
    if not s.dry_run:
        t.add_row("Output", "", str(outdir))
    converted = "would convert" if s.dry_run else "converted"
    t.add_row("Messages", f"{s.ok:,}", f"{converted} ({s.body_plain:,} plain, {s.body_html:,} HTML)")
    if s.skipped:
        t.add_row("", f"{s.skipped:,}", "skipped by label")
    if s.out_of_range:
        t.add_row("", f"{s.out_of_range:,}", "outside --since/--until")
    if s.already_converted:
        t.add_row("", f"{s.already_converted:,}", "already converted (--incremental)")
    if s.duplicate_message_ids_skipped:
        t.add_row("", f"{s.duplicate_message_ids_skipped:,}", "duplicate Message-IDs (labels merged)")
    if s.labels_updated:
        t.add_row("", f"{s.labels_updated:,}", "with updated labels")
    if s.errors:
        where = "" if s.dry_run else " (raw copies in _unparsed/)"
        t.add_row("[red]Failed[/]", f"[red]{s.errors:,}[/]", f"could not be converted{where}")
    if s.attachment_refs:
        saved = s.attachment_bytes_referenced - s.attachment_bytes_stored
        pct = 100 * saved / s.attachment_bytes_referenced if s.attachment_bytes_referenced else 0
        t.add_row(
            "Attachments",
            f"{s.unique_attachments:,}",
            f"unique files, {human_bytes(s.attachment_bytes_stored)} "
            f"({s.attachment_refs:,} references; dedup saved {human_bytes(saved)}, {pct:.0f}%)",
        )
    t.add_row("Markdown", human_bytes(s.markdown_bytes), "")
    if s.pruned:
        t.add_row("Pruned", f"{s.pruned:,}", "files from earlier runs")
    elif s.stale_files:
        t.add_row(
            "[yellow]Stale[/]", f"{s.stale_files:,}", "files from earlier runs not produced now; --prune deletes them"
        )
    if s.elapsed_s >= 0.1:
        rate = f"{s.processed / s.elapsed_s:,.0f} messages/s, "
        t.add_row("Time", f"{s.elapsed_s:,.1f} s", f"{rate}{human_bytes(s.bytes_total)} input")
    else:
        t.add_row("Time", "< 0.1 s", f"{human_bytes(s.bytes_total)} input")
    console.print(t)
