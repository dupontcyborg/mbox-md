"""Re-runs (incremental, prune), the failure report, label merging, timezones, and non-Gmail mbox files."""

import json
import shutil
from pathlib import Path

import pytest

from mbox_md import ConvertOptions, convert
from mbox_md._manifest import read_manifest

FIXTURES = Path(__file__).parent / "fixtures"


def opts(**kw):
    return ConvertOptions(workers=1, **kw)


def front_matter(md: Path) -> dict:
    block = md.read_text(encoding="utf-8").split("---\n")[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in block.strip().splitlines())}


def md_files(out: Path) -> dict[str, Path]:
    files = [p for p in out.rglob("*.md") if "attachments" not in p.relative_to(out).parts]
    return {front_matter(p)["subject"]: p for p in files if p.read_text(encoding="utf-8").startswith("---\n")}


# --- manifest --------------------------------------------------------------------------------------------------


def test_manifest_has_everything_needed_to_rebuild_the_index(tmp_path):
    convert(FIXTURES / "attachments.mbox", tmp_path, opts())
    entries = read_manifest(tmp_path)
    assert len(entries) == 10 and all(e.key and e.path and e.body in {"plain", "html", "none"} for e in entries)
    report = next(e for e in entries if e.subject == "Report attached")
    assert report.sender.endswith("<alice@example.com>") and report.labels == ["Inbox"] and report.date
    [att] = report.attachments
    assert (tmp_path / att.path).is_file() and att.name == "report.pdf" and att.size > 0
    indexed = {json.loads(line)["path"] for line in (tmp_path / "attachments" / "index.jsonl").read_text().splitlines()}
    assert {a.path for e in entries for a in e.attachments} == indexed


def test_body_kind_is_in_the_front_matter(tmp_path):
    convert(FIXTURES / "attachments.mbox", tmp_path, opts())
    files = md_files(tmp_path)
    assert front_matter(files["Attachment only"])["body"] == "none"
    assert front_matter(files["Report attached"])["body"] == "plain"
    assert front_matter(files["Inline images"])["body"] == "html"


# --- incremental and resume ------------------------------------------------------------------------------------


def test_incremental_converts_only_new_messages(tmp_path):
    first = convert(FIXTURES / "basic.mbox", tmp_path, opts(limit=3))
    assert first.ok == 3
    second = convert(FIXTURES / "basic.mbox", tmp_path, opts(incremental=True))
    assert (second.ok, second.already_converted) == (5, 3)
    assert len(read_manifest(tmp_path)) == 8 and len(md_files(tmp_path)) == 8
    third = convert(FIXTURES / "basic.mbox", tmp_path, opts(incremental=True))
    assert (third.ok, third.already_converted) == (0, 8)


def test_incremental_resumes_after_a_killed_run(tmp_path):
    convert(FIXTURES / "attachments.mbox", tmp_path, opts(limit=4))
    manifest = tmp_path / "messages.jsonl"
    manifest.write_text(manifest.read_text() + '{"key": "half-writ', encoding="utf-8")  # killed mid-line
    (tmp_path / "attachments" / "index.jsonl").unlink()  # the index is only written at the end
    stats = convert(FIXTURES / "attachments.mbox", tmp_path, opts(incremental=True))
    assert (stats.ok, stats.already_converted) == (6, 4)
    lines = manifest.read_text().splitlines()
    assert len(lines) == 10 and all(json.loads(line) for line in lines)
    # The index covers attachments from both runs.
    index = [json.loads(line) for line in (tmp_path / "attachments" / "index.jsonl").read_text().splitlines()]
    assert sum(len(e["messages"]) for e in index) == stats.attachment_refs + 4


def test_incremental_updates_labels_that_changed(tmp_path):
    convert(FIXTURES / "basic.mbox", tmp_path, opts())
    newer = tmp_path / "newer.mbox"
    newer.write_bytes(
        (FIXTURES / "basic.mbox").read_bytes().replace(b"X-Gmail-Labels: Inbox", b"X-Gmail-Labels: Archived", 1)
    )
    stats = convert(newer, tmp_path, opts(incremental=True))
    assert (stats.ok, stats.labels_updated) == (0, 1)
    assert front_matter(md_files(tmp_path)["Plain text only"])["labels"] == ["Archived"]
    assert next(e for e in read_manifest(tmp_path) if e.subject == "Plain text only").labels == ["Archived"]


def test_incremental_and_prune_are_exclusive():
    with pytest.raises(ValueError, match="can't be combined"):
        ConvertOptions(incremental=True, prune=True)


# --- stale files and prune -------------------------------------------------------------------------------------


def test_stale_files_are_reported_then_pruned(tmp_path):
    out = tmp_path / "out"
    convert(FIXTURES / "attachments.mbox", out, opts())
    notes = out / "my-notes.md"
    notes.write_text("not written by mbox-md")
    before = {p for p in out.rglob("*") if p.is_file()}

    stats = convert(FIXTURES / "basic.mbox", out, opts())  # a different mailbox into the same folder
    assert stats.stale_files > 0 and stats.pruned == 0
    assert before <= {p for p in out.rglob("*") if p.is_file()}  # nothing deleted without --prune

    stats = convert(FIXTURES / "basic.mbox", out, opts(prune=True))
    assert stats.pruned > 0 and stats.stale_files == 0
    assert notes.exists()  # never recorded by mbox-md, so never pruned
    assert not (out / "attachments").exists() or not any((out / "attachments").rglob("*.pdf"))
    assert "Report attached" not in md_files(out) and len(md_files(out)) == 8 == len(read_manifest(out))


def test_prune_never_leaves_the_output_folder(tmp_path):
    out, outside = tmp_path / "out", tmp_path / "precious.txt"
    outside.write_text("keep me")
    convert(FIXTURES / "basic.mbox", out, opts())
    manifest = out / "messages.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0]["path"] = "../precious.txt"  # a tampered or corrupted manifest
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    convert(FIXTURES / "basic.mbox", out, opts(prune=True))
    assert outside.read_text() == "keep me"


# --- failure report --------------------------------------------------------------------------------------------


def test_failures_are_reported_and_cleared_once_fixed(tmp_path, monkeypatch):
    from mbox_md import _pipeline

    real = _pipeline.parse_or_skip

    def flaky(raw, options=None):
        if b"Plain text only" in raw:
            raise ValueError("synthetic failure")
        return real(raw, options)

    monkeypatch.setattr(_pipeline, "parse_or_skip", flaky)
    stats = convert(FIXTURES / "basic.mbox", tmp_path, opts())
    assert stats.errors == 1
    [row] = [json.loads(line) for line in (tmp_path / "_unparsed" / "errors.jsonl").read_text().splitlines()]
    assert "synthetic failure" in row["error"] and row["message_id"].startswith("<basic.1@")
    assert (tmp_path / row["path"]).read_bytes().startswith(b"From: ")

    monkeypatch.setattr(_pipeline, "parse_or_skip", real)
    stats = convert(FIXTURES / "basic.mbox", tmp_path, opts(incremental=True))  # retries only the failed one
    assert (stats.ok, stats.errors, stats.already_converted) == (1, 0, 7)
    assert not (tmp_path / "_unparsed" / "errors.jsonl").exists()


# --- duplicates and labels -------------------------------------------------------------------------------------


def test_spam_copy_does_not_hide_the_inbox_copy(tmp_path):
    convert(FIXTURES / "labels.mbox", tmp_path, opts())
    assert front_matter(md_files(tmp_path)["Spam copy first"])["labels"] == ["Inbox", "Starred"]


def test_duplicate_labels_are_merged_into_the_kept_copy(tmp_path):
    stats = convert(FIXTURES / "labels.mbox", tmp_path, opts())
    assert stats.labels_updated == 1
    assert front_matter(md_files(tmp_path)["Duplicate, first copy"])["labels"] == ["Inbox", "Sent"]


# --- timezones -------------------------------------------------------------------------------------------------


def test_tz_utc_moves_late_night_mail_to_the_next_day(tmp_path):
    convert(FIXTURES / "encodings.mbox", tmp_path / "sender", opts())
    convert(FIXTURES / "encodings.mbox", tmp_path / "utc", opts(tz="utc"))
    sender = md_files(tmp_path / "sender")["Sent from UTC-8 late at night"]
    utc = md_files(tmp_path / "utc")["Sent from UTC-8 late at night"]
    assert sender.relative_to(tmp_path / "sender").as_posix().startswith("2024/01/2024-01-31-2345-")
    assert utc.relative_to(tmp_path / "utc").as_posix().startswith("2024/02/2024-02-01-0745-")
    assert front_matter(utc)["date"] == front_matter(sender)["date"] == "2024-01-31T23:45:00-08:00"


def test_tz_applies_to_date_filters(tmp_path):
    day = __import__("datetime").date(2024, 2, 1)
    assert convert(FIXTURES / "encodings.mbox", tmp_path / "a", opts(since=day)).ok == 0
    assert convert(FIXTURES / "encodings.mbox", tmp_path / "b", opts(since=day, tz="utc")).ok == 1
    assert convert(FIXTURES / "encodings.mbox", tmp_path / "c", opts(since=day, tz="Asia/Tokyo")).ok == 1


def test_bad_timezone_is_rejected():
    with pytest.raises(ValueError, match="unknown timezone"):
        ConvertOptions(tz="Mars/Olympus_Mons")


# --- non-Gmail mbox files --------------------------------------------------------------------------------------


def test_mail_client_mbox_with_mboxrd_escaping(tmp_path):
    stats = convert(FIXTURES / "mailclient.mbox", tmp_path, opts())
    assert stats.ok == 3
    body = md_files(tmp_path)["Client-written message"].read_text(encoding="utf-8")
    assert "\nFrom the escaped line" in body and "\n>From a doubly escaped line." in body


def test_keep_from_escapes_for_mboxo(tmp_path):
    convert(FIXTURES / "mailclient.mbox", tmp_path, opts(unescape_from=False))
    body = md_files(tmp_path)["Client-written message"].read_text(encoding="utf-8")
    assert "\n>From the escaped line" in body


# --- CLI -------------------------------------------------------------------------------------------------------


def test_cli_rerun_flags(tmp_path, capsys):
    from mbox_md._cli import main

    out = tmp_path / "out"
    src = tmp_path / "a.mbox"
    shutil.copy(FIXTURES / "attachments.mbox", src)
    assert main([str(src), str(out), "--workers", "1", "-q"]) == 0
    shutil.copy(FIXTURES / "basic.mbox", src)
    assert main([str(src), str(out), "--workers", "1"]) == 0
    assert "--prune deletes them" in " ".join(capsys.readouterr().out.split())
    assert main([str(src), str(out), "--workers", "1", "--prune"]) == 0
    assert "Pruned" in capsys.readouterr().out
    assert main([str(src), str(out), "--workers", "1", "--incremental"]) == 0
    assert "already converted" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([str(src), str(out), "--incremental", "--prune"])
    with pytest.raises(SystemExit):
        main([str(src), str(out), "--tz", "Nowhere/Special"])
    assert "unknown timezone" in capsys.readouterr().err
