import json
import multiprocessing
import os
import re
import subprocess
import sys
from urllib.parse import unquote

import pytest
from conftest import make_message

from mbox_md import ConvertOptions, WorkerError, convert
from mbox_md._cli import main
from mbox_md._pipeline import _raise_worker_errors, run_parallel

OPTS = ConvertOptions(workers=1)


def test_end_to_end(mbox_file, tmp_path):
    src = mbox_file(
        make_message(message_id="<1@x>", subject="First", attachments=[("a.txt", "text/plain", b"shared")]),
        make_message(message_id="<2@x>", subject="Second", attachments=[("b.txt", "text/plain", b"shared")]),
        make_message(message_id="<3@x>", subject="Junk", labels="Spam"),
        make_message(message_id="<1@x>", subject="First again"),
    )
    out = tmp_path / "out"
    stats = convert(src, out, OPTS)

    assert (stats.ok, stats.skipped, stats.errors, stats.duplicate_message_ids_skipped) == (2, 1, 0, 1)
    assert stats.unique_attachments == 1 and stats.attachment_refs == 2
    assert stats.attachment_bytes_referenced == 12 and stats.attachment_bytes_stored == 6

    manifest = [json.loads(line) for line in (out / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(r["subject"] for r in manifest) == ["First", "Second"]

    [entry] = [
        json.loads(line) for line in (out / "attachments" / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert entry["names"] == {"a.txt": 1, "b.txt": 1} and len(entry["messages"]) == 2

    for md in out.glob("2023/11/*.md"):
        for link in re.findall(r"\]\((\.\./[^)]+)\)", md.read_text(encoding="utf-8")):
            assert (md.parent / unquote(link)).resolve().is_file()


def test_limit(mbox_file, tmp_path):
    src = mbox_file(*(make_message(message_id=f"<{i}@x>") for i in range(5)))
    assert convert(src, tmp_path / "out", ConvertOptions(workers=1, limit=3)).ok == 3


def test_no_attachments_writes_no_attachment_dir(mbox_file, tmp_path):
    convert(mbox_file(make_message()), tmp_path / "out", OPTS)
    assert not (tmp_path / "out" / "attachments").exists()


def test_cli_prints_stats_json(mbox_file, tmp_path, capsys):
    assert main([str(mbox_file(make_message())), str(tmp_path / "out"), "--workers", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] == 1


def test_parallel_workers_match_in_process(mbox_file, tmp_path):
    msgs = [make_message(message_id=f"<{i}@x>", subject=f"Message {i}") for i in range(40)]
    src = mbox_file(*msgs)
    serial = convert(src, tmp_path / "serial", ConvertOptions(workers=1))
    parallel = convert(src, tmp_path / "parallel", ConvertOptions(workers=3))
    assert (serial.ok, parallel.ok) == (40, 40)
    files = lambda d: sorted(str(p.relative_to(d)) for p in d.rglob("*.md"))  # noqa: E731
    assert files(tmp_path / "serial") == files(tmp_path / "parallel")


def _die(chunk):
    os._exit(1)


def test_crashed_worker_raises_worker_error():
    with pytest.raises(WorkerError, match="worker process failed"):
        list(_raise_worker_errors(run_parallel(_die, iter([b"x"]), 2)))


@pytest.mark.skipif(
    multiprocessing.get_start_method() != "spawn", reason="only spawned workers re-import __main__ (macOS, Windows)"
)
def test_unstartable_workers_raise_instead_of_hanging(mbox_file, tmp_path):
    # From stdin, spawned workers can't re-import __main__. This used to hang forever.
    src = mbox_file(make_message())
    code = (
        f"import mbox_md; mbox_md.convert({str(src)!r}, {str(tmp_path / 'out')!r}, mbox_md.ConvertOptions(workers=2))"
    )
    r = subprocess.run([sys.executable, "-"], input=code, text=True, capture_output=True, timeout=60)
    assert r.returncode != 0 and "WorkerError" in r.stderr
