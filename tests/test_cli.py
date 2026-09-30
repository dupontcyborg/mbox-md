"""The command line, run in-process through `main(argv)`. Output isn't a terminal here, so no progress bar."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from mbox_md._cli import main, parse_day, parse_labels, parse_size
from mbox_md._reader import MboxStream, ReadError

FIXTURES = Path(__file__).parent / "fixtures"


def run(capsys, *args):
    code = main([*map(str, args), "--workers", "1"])
    out, err = capsys.readouterr()
    return code, out, err


def run_json(capsys, *args):
    code, out, _ = run(capsys, *args, "--json")
    assert code == 0
    return json.loads(out)


def bodies(outdir: Path) -> dict[str, str]:
    result = {}
    for md in outdir.glob("20*/*/*.md"):
        text = md.read_text(encoding="utf-8")
        subject = json.loads(next(line for line in text.splitlines() if line.startswith("subject: "))[9:])
        result[subject] = text.split("---\n", 2)[2]
    return result


# --- argument parsing ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"), [("0", 0), ("5KB", 5120), ("5k", 5120), ("1.5 MB", 1572864), ("200", 200)]
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_parse_size_and_day_reject_garbage():
    with pytest.raises(Exception, match="not a size"):
        parse_size("lots")
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        parse_day("01/15/2024")
    assert parse_day("2024-01-15") == date(2024, 1, 15)


def test_parse_labels():
    assert parse_labels("Spam, Trash,,Promotions ") == {"Spam", "Trash", "Promotions"}
    assert parse_labels("") == frozenset()


def test_outdir_required_without_dry_run(capsys):
    with pytest.raises(SystemExit) as e:
        main([str(FIXTURES / "basic.mbox")])
    assert e.value.code == 2 and "outdir is required" in capsys.readouterr().err


def test_since_after_until_is_rejected(capsys, tmp_path):
    with pytest.raises(SystemExit):
        main([str(FIXTURES / "basic.mbox"), str(tmp_path), "--since", "2024-02-01", "--until", "2024-01-01"])
    assert "--since is after --until" in capsys.readouterr().err


# --- output modes ----------------------------------------------------------------------------------------------


def test_default_output_is_a_summary(capsys, tmp_path):
    code, out, err = run(capsys, FIXTURES / "labels.mbox", tmp_path / "out")
    assert code == 0
    assert "Messages" in out and "converted" in out and "skipped by label" in out and "duplicate" in out
    assert "".join(str(tmp_path / "out").split()) in "".join(out.split())


def test_quiet_prints_nothing(capsys, tmp_path):
    code, out, err = run(capsys, FIXTURES / "basic.mbox", tmp_path / "out", "--quiet")
    assert (code, out, err) == (0, "", "")
    assert len(list((tmp_path / "out").glob("2024/01/*.md"))) == 8


def test_json_is_the_only_stdout(capsys, tmp_path):
    stats = run_json(capsys, FIXTURES / "basic.mbox", tmp_path / "out")
    assert stats["ok"] == 8 and stats["bytes_read"] == stats["bytes_total"] > 0 and stats["markdown_bytes"] > 0


def test_missing_input_is_a_clear_error(capsys, tmp_path):
    code, out, err = run(capsys, tmp_path / "nope.mbox", tmp_path / "out")
    assert code == 1 and "no such file" in err and out == ""


def test_failures_are_summarized_and_detailed_with_verbose(capsys, tmp_path, monkeypatch):
    def boom(raw, options=None):
        raise ValueError("synthetic failure")

    monkeypatch.setattr("mbox_md._pipeline.parse_or_skip", boom)
    code, out, err = run(capsys, FIXTURES / "crlf.mbox", tmp_path / "a")
    assert code == 0 and "2 message(s) failed" in err and "--verbose" in err and "synthetic failure" not in err
    code, out, err = run(capsys, FIXTURES / "crlf.mbox", tmp_path / "b", "--verbose")
    assert err.count("synthetic failure") == 2 and "_unparsed" in err


def test_python_dash_m_returns_exit_code(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "mbox_md", str(tmp_path / "nope.mbox"), str(tmp_path)], capture_output=True
    )
    assert r.returncode == 1


# --- flags that change what's converted ------------------------------------------------------------------------


def test_dry_run_writes_nothing(capsys, tmp_path):
    out = tmp_path / "out"
    stats = run_json(capsys, FIXTURES / "attachments.mbox", out, "--dry-run")
    assert not out.exists()
    assert stats["dry_run"] and stats["ok"] == 10 and stats["unique_attachments"] > 0
    real = run_json(capsys, FIXTURES / "attachments.mbox", out)
    for key in ("ok", "unique_attachments", "attachment_bytes_stored", "markdown_bytes"):
        assert stats[key] == real[key], key


def test_dry_run_needs_no_outdir(capsys):
    code, out, _ = run(capsys, FIXTURES / "basic.mbox", "--dry-run")
    assert code == 0 and "Dry run" in out


def test_since_and_until(capsys, tmp_path):
    stats = run_json(
        capsys, FIXTURES / "encodings.mbox", tmp_path / "out", "--since", "2024-01-31", "--until", "2024-01-31"
    )
    assert stats["ok"] == 1 and stats["out_of_range"] == 10  # includes the two undated messages
    assert list(bodies(tmp_path / "out")) == ["Sent from UTC-8 late at night"]


def test_skip_labels_empty_keeps_spam_and_trash(capsys, tmp_path):
    stats = run_json(capsys, FIXTURES / "labels.mbox", tmp_path / "out", "--skip-labels", "")
    assert (stats["ok"], stats["skipped"]) == (10, 0)


def test_skip_labels_custom(capsys, tmp_path):
    stats = run_json(capsys, FIXTURES / "labels.mbox", tmp_path / "out", "--skip-labels", "Important")
    assert stats["skipped"] == 1 and "Spam message" in bodies(tmp_path / "out")


def test_min_inline_image(capsys, tmp_path):
    default = run_json(capsys, FIXTURES / "attachments.mbox", tmp_path / "a")
    keep_all = run_json(capsys, FIXTURES / "attachments.mbox", tmp_path / "b", "--min-inline-image", "0")
    assert keep_all["attachment_refs"] == default["attachment_refs"] + 1  # the 300-byte inline pixel


def test_strip_quotes(capsys, tmp_path):
    run_json(capsys, FIXTURES / "basic.mbox", tmp_path / "kept")
    run_json(capsys, FIXTURES / "basic.mbox", tmp_path / "stripped", "--strip-quotes")
    assert "> Shall we meet?" in bodies(tmp_path / "kept")["Reply quoting"]
    stripped = bodies(tmp_path / "stripped")["Reply quoting"]
    assert "Sounds good." in stripped and ">" not in stripped and "wrote:" not in stripped


def test_limit(capsys, tmp_path):
    assert run_json(capsys, FIXTURES / "basic.mbox", tmp_path / "out", "--limit", "3")["ok"] == 3


# --- the reader behind the progress bar ------------------------------------------------------------------------


def test_missing_file_raises_read_error(tmp_path):
    with pytest.raises(ReadError, match="no such file"):
        MboxStream(tmp_path / "missing.mbox")


def test_workers_must_be_positive(capsys, tmp_path):
    with pytest.raises(SystemExit):
        main([str(FIXTURES / "basic.mbox"), str(tmp_path), "--workers", "0"])
    assert "--workers must be at least 1" in capsys.readouterr().err


def test_worker_error_exits_1(capsys, tmp_path, monkeypatch):
    from mbox_md import WorkerError

    def broken(*args, **kwargs):
        raise WorkerError("a worker process failed to start")

    monkeypatch.setattr("mbox_md._cli.convert", broken)
    code, _, err = run(capsys, FIXTURES / "basic.mbox", tmp_path / "out")
    assert code == 1 and "worker process failed" in err


def test_ctrl_c_exits_130_and_points_at_partial_output(capsys, tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("mbox_md._cli.convert", interrupted)
    code, _, err = run(capsys, FIXTURES / "basic.mbox", tmp_path / "out")
    assert code == 130 and "interrupted" in err and "partial output" in "".join(err.split("\n"))


def test_summary_shows_range_attachments_and_timing(capsys, tmp_path, monkeypatch):
    from mbox_md import ConvertStats

    stats = ConvertStats(
        ok=5,
        out_of_range=3,
        errors=1,
        attachment_refs=4,
        unique_attachments=2,
        attachment_bytes_referenced=4000,
        attachment_bytes_stored=1000,
        elapsed_s=2.0,
        bytes_total=9,
    )
    monkeypatch.setattr("mbox_md._cli.convert", lambda *a, **k: stats)
    code, out, _ = run(capsys, FIXTURES / "basic.mbox", tmp_path / "out")
    flat = " ".join(out.split())
    assert "outside --since/--until" in flat and "dedup saved 3.0 KB, 75%" in flat
    assert "Failed" in flat and "4 messages/s" in flat


def test_progress_bar_path_runs_when_output_is_a_terminal(tmp_path, monkeypatch):
    # Force rich to treat stderr as a terminal so the progress callbacks run.
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "120")
    r = subprocess.run(
        [sys.executable, "-m", "mbox_md", str(FIXTURES / "basic.mbox"), str(tmp_path / "out"), "--workers", "1"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "Converting" in r.stderr and "100%" in r.stderr and "messages" in r.stderr


def test_stream_must_be_entered(tmp_path):
    with pytest.raises(RuntimeError, match="context manager"):
        list(MboxStream(FIXTURES / "basic.mbox"))
