from datetime import UTC, datetime
from pathlib import Path

from mbox_md._naming import message_relpath, safe_name, slugify


def test_slugify_strips_reply_prefixes_and_punctuation():
    assert slugify("Re: Fwd: RE: Quarterly Plan (v2)!") == "quarterly-plan-v2"


def test_slugify_transliterates_unicode():
    assert slugify("Café résumé") == "cafe-resume"


def test_slugify_empty_and_non_latin():
    assert slugify("") == "no-subject"
    assert slugify(None) == "no-subject"
    assert slugify("日本語") == "no-subject"


def test_slugify_truncates_without_trailing_dash():
    s = slugify("word " * 40)
    assert len(s) <= 60 and not s.endswith("-")


def test_safe_name_removes_path_separators_and_control_chars():
    assert safe_name("../../etc/passwd") == ".._.._etc_passwd"
    assert safe_name("a\x00b") == "a_b"
    assert safe_name("  ") == "attachment"


def test_message_relpath_dated():
    dt = datetime(2024, 3, 5, 9, 7, tzinfo=UTC)
    assert message_relpath(dt, "Hello", "abcdef") == Path("2024/03/2024-03-05-0907-hello-abcd.md")


def test_message_relpath_undated():
    assert message_relpath(None, "", "abcdef") == Path("undated/undated-0000-no-subject-abcd.md")
