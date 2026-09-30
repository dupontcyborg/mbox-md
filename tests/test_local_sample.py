"""Smoke test against the gitignored real-mail sample. Asserts structure and counts only, never content."""

import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from mbox_md import ConvertOptions, convert

SAMPLE = Path(__file__).parent.parent / "local-data" / "takeout-sample.mbox"

pytestmark = [
    pytest.mark.local,
    pytest.mark.skipif(not SAMPLE.exists(), reason="local-data/takeout-sample.mbox not present"),
]


def test_sample_converts_cleanly(tmp_path):
    out = tmp_path / "out"
    stats = convert(SAMPLE, out, ConvertOptions(workers=2))
    assert stats.errors == 0
    assert (stats.ok, stats.skipped) == (143, 2)
    assert stats.unique_attachments == 22
    assert len((out / "messages.jsonl").read_text(encoding="utf-8").splitlines()) == stats.ok

    mds = [p for p in out.rglob("*.md") if "attachments" not in p.parts]
    assert len(mds) == stats.ok
    for md in mds:
        text = md.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        for link in re.findall(r"\]\((\.\./attachments/[^)]+)\)", text):
            assert (md.parent / unquote(link)).resolve().is_file()
    for line in (out / "attachments" / "index.jsonl").read_text(encoding="utf-8").splitlines():
        assert (out / json.loads(line)["path"]).is_file()
