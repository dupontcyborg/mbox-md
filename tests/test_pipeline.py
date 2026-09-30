import json
import re
from urllib.parse import unquote

from conftest import make_message

from mbox_md import ConvertOptions, convert
from mbox_md.cli import main

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

    manifest = [json.loads(line) for line in (out / "messages.jsonl").read_text().splitlines()]
    assert sorted(r["subject"] for r in manifest) == ["First", "Second"]

    [entry] = [json.loads(line) for line in (out / "attachments" / "index.jsonl").read_text().splitlines()]
    assert entry["names"] == {"a.txt": 1, "b.txt": 1} and len(entry["messages"]) == 2

    for md in out.glob("2023/11/*.md"):
        for link in re.findall(r"\]\((\.\./[^)]+)\)", md.read_text()):
            assert (md.parent / unquote(link)).resolve().is_file()


def test_limit(mbox_file, tmp_path):
    src = mbox_file(*(make_message(message_id=f"<{i}@x>") for i in range(5)))
    assert convert(src, tmp_path / "out", ConvertOptions(workers=1, limit=3)).ok == 3


def test_no_attachments_writes_no_attachment_dir(mbox_file, tmp_path):
    convert(mbox_file(make_message()), tmp_path / "out", OPTS)
    assert not (tmp_path / "out" / "attachments").exists()


def test_cli_prints_stats_json(mbox_file, tmp_path, capsys):
    main([str(mbox_file(make_message())), str(tmp_path / "out"), "--workers", "1"])
    assert json.loads(capsys.readouterr().out)["ok"] == 1
