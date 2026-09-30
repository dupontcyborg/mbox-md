"""Command-line interface. A thin layer over `mbox_md.convert`."""

import argparse
import json
import sys

from . import __version__
from ._options import ConvertOptions, default_workers
from ._pipeline import ConvertStats, convert


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mbox-md", description="Convert an mbox archive into Markdown files.")
    ap.add_argument("mbox", help="input .mbox (or .mbox.zst)")
    ap.add_argument("outdir", help="output folder")
    ap.add_argument("--limit", type=int, help="stop after this many messages")
    ap.add_argument("--workers", type=int, default=default_workers(), help="worker processes (default: CPUs - 1)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    options = ConvertOptions(workers=a.workers, limit=a.limit)

    def progress(stats: ConvertStats) -> None:
        if stats.processed % 5000 == 0:
            print(f"{stats.processed} processed, {stats.elapsed_s:.0f}s", file=sys.stderr, flush=True)

    def error(path: str, err: str) -> None:
        print("ERROR", path, err, file=sys.stderr)

    stats = convert(a.mbox, a.outdir, options, on_progress=progress, on_error=error)
    print(json.dumps(stats.as_dict(), indent=1))
