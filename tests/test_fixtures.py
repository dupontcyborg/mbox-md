"""End-to-end tests over the committed .mbox fixtures in tests/fixtures/ (all fabricated, see generate.py)."""

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from mbox_md import ConvertOptions, convert

FIXTURES = Path(__file__).parent / "fixtures"
RESERVED_DOMAIN = re.compile(r"(^|\.)(example\.(com|org|net)|test|invalid|example)$")


def load_generator():
    spec = importlib.util.spec_from_file_location("generate", FIXTURES / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Converted:
    def __init__(self, out: Path, stats):
        self.out, self.stats = out, stats
        self.messages = {}
        for md in out.rglob("*.md"):
            if md.parts[len(out.parts)] == "attachments":
                continue
            text = md.read_text(encoding="utf-8")
            front = dict(line.split(": ", 1) for line in text.split("---\n")[1].strip().splitlines())
            fm = {k: json.loads(v) for k, v in front.items()}
            self.messages[fm["subject"]] = {"path": md, "text": text, "fm": fm, "body": text.split("---\n", 2)[2]}

    def __getitem__(self, subject):
        return self.messages[subject]

    def attachment_files(self):
        root = self.out / "attachments"
        return sorted(p for p in root.rglob("*") if p.is_file() and p.name != "index.jsonl") if root.exists() else []

    def index(self):
        return [json.loads(line) for line in (self.out / "attachments" / "index.jsonl").read_text().splitlines()]


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    cache = {}

    def run(name: str) -> Converted:
        if name not in cache:
            out = tmp_path_factory.mktemp(name)
            cache[name] = Converted(out, convert(FIXTURES / f"{name}.mbox", out, ConvertOptions(workers=1)))
        return cache[name]

    return run


# --- the fixtures themselves -----------------------------------------------------------------------------------


def test_committed_fixtures_match_the_generator():
    rendered = load_generator().render_all()
    committed = {p.name: p.read_bytes() for p in FIXTURES.glob("*.mbox")}
    assert committed.keys() == rendered.keys(), "run: uv run python tests/fixtures/generate.py"
    for name, data in rendered.items():
        assert committed[name] == data, f"{name} is stale or hand-edited; run: uv run python tests/fixtures/generate.py"


def test_fixtures_only_use_reserved_domains():
    for path in FIXTURES.glob("*.mbox"):
        for domain in re.findall(rb"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", path.read_bytes()):
            assert RESERVED_DOMAIN.search(domain.decode()), f"{path.name} contains a non-reserved domain: {domain!r}"


def test_no_other_mail_files_in_fixtures():
    allowed = {"generate.py", "README.md"}
    for p in FIXTURES.iterdir():
        assert p.suffix == ".mbox" or p.name in allowed or p.name == "__pycache__", f"unexpected fixture file {p.name}"


# --- basic -----------------------------------------------------------------------------------------------------


def test_basic(converted):
    c = converted("basic")
    assert (c.stats.ok, c.stats.errors, c.stats.body_plain, c.stats.body_html) == (8, 0, 7, 1)
    assert c["Plain text only"]["body"].strip().endswith("This is a plain text message.\n\nAlice")
    assert "Hello **Bob**, this one is *HTML only*." in c["HTML only"]["body"]
    assert "The plain version." in c["Multipart alternative"]["body"]
    assert "HTML version" not in c["Multipart alternative"]["body"]
    assert c["With Cc"]["fm"]["cc"] == ["dave@example.com"]
    assert c["With Cc"]["fm"]["to"] == ["bob@example.org", "carol@example.net"]
    assert "> Shall we meet?" in c["Reply quoting"]["body"]


def test_basic_filenames(converted):
    c = converted("basic")
    names = {s: m["path"].name for s, m in c.messages.items()}
    assert re.fullmatch(
        r"2024-01-15-1330-quarterly-plan-v2-[0-9a-f]{4}\.md", names["Re: Fwd: RE: Quarterly plan (v2)!"]
    )
    assert names["Café résumé naïve"].startswith("2024-01-15-1430-cafe-resume-naive-")
    long_slug = names["Weekly sync " * 12][len("2024-01-15-1530-") : -len("-abcd.md")]
    assert len(long_slug) <= 60 and not long_slug.endswith("-")
    assert all(m["path"].parent.relative_to(c.out) == Path("2024/01") for m in c.messages.values())


# --- labels ----------------------------------------------------------------------------------------------------


def test_labels_and_dedup(converted):
    c = converted("labels")
    assert (c.stats.ok, c.stats.skipped, c.stats.duplicate_message_ids_skipped) == (7, 2, 1)
    assert "Spam message" not in c.messages and "Trashed message" not in c.messages
    assert c["In the inbox"]["fm"]["labels"] == ["Inbox", "Important", "Opened"]
    assert c["Folded labels"]["fm"]["labels"] == ["Inbox", "Category Updates", "Opened"]
    assert c["No labels header"]["fm"]["labels"] == []
    assert c["Archived, custom label"]["fm"]["labels"] == ["Archived", "Receipts/2024"]
    assert "Duplicate, first copy" in c.messages and "Duplicate, second copy" not in c.messages
    assert {"No Message-ID one", "No Message-ID two"} <= c.messages.keys()


def test_skip_labels_can_be_disabled(tmp_path):
    stats = convert(FIXTURES / "labels.mbox", tmp_path, ConvertOptions(workers=1, skip_labels=frozenset()))
    assert (stats.ok, stats.skipped) == (9, 0)


@pytest.mark.xfail(strict=True, reason="TODO: labels from duplicate Message-IDs are not merged")
def test_duplicate_labels_are_merged(converted):
    assert converted("labels")["Duplicate, first copy"]["fm"]["labels"] == ["Inbox", "Sent"]


# --- attachments -----------------------------------------------------------------------------------------------


def attachment_names(message):
    return re.findall(r"^- \[([^\]]+)\]\(", message["body"], re.M)


def test_attachments(converted):
    c = converted("attachments")
    assert c.stats.errors == 0
    assert attachment_names(c["Report attached"]) == ["report.pdf"]
    assert attachment_names(c["Unnamed octet-stream"]) == ["attachment.pdf"]  # extension sniffed from %PDF
    assert attachment_names(c["Path in filename"]) == [".._.._etc_passwd"]
    assert attachment_names(c["Calendar invite"]) == ["invite.ics"]
    assert "_(no body)_" in c["Attachment only"]["body"] and attachment_names(c["Attachment only"]) == ["notes.txt"]


def test_same_bytes_share_one_file_and_same_name_different_bytes_do_not(converted):
    c = converted("attachments")
    first = c["Report attached"]["fm"]["attachments"]
    assert c["Same bytes, new name"]["fm"]["attachments"] == first
    assert c["Same name, new bytes"]["fm"]["attachments"] != first
    [entry] = [e for e in c.index() if len(e["messages"]) >= 2]
    assert set(entry["names"]) >= {"report.pdf", "copy-of-report.pdf"}


def test_small_inline_images_dropped_but_attached_ones_kept(converted):
    names = attachment_names(converted("attachments")["Inline images"])
    assert "pixel.png" not in names
    assert sorted(names) == ["photo.png", "tiny-but-attached.png"]


def test_attachment_links_and_index_resolve(converted):
    c = converted("attachments")
    for m in c.messages.values():
        for link in re.findall(r"\]\((\.\./[^)]+)\)", m["body"]):
            assert (m["path"].parent / unquote(link)).resolve().is_file()
    for entry in c.index():
        assert (c.out / entry["path"]).is_file()


@pytest.mark.xfail(strict=True, reason="TODO bug: same bytes with a different extension are stored twice")
def test_same_bytes_with_junk_extension_share_one_file(converted):
    c = converted("attachments")
    assert c["Same bytes, junk extension"]["fm"]["attachments"] == c["Report attached"]["fm"]["attachments"]
    assert len(c.attachment_files()) == len(c.index())


@pytest.mark.xfail(strict=True, reason="TODO bug: message/rfc822 parts are skipped as multipart, so forwards vanish")
def test_forwarded_message_is_kept(converted):
    assert attachment_names(converted("attachments")["Fwd: The original"])


# --- html ------------------------------------------------------------------------------------------------------


def test_layout_newsletter(converted):
    body = converted("html")["Layout newsletter"]["body"]
    assert "# Monthly update" in body
    assert "First paragraph of the newsletter.\n\nSecond paragraph" in body
    assert "[link](https://example.com/post)" in body
    assert "Nested footer text" in body and "[Unsubscribe](https://example.com/unsubscribe)" in body
    for leaked in ("Newsletter tab title", "@media", ".btn", "logo.png", "Example Co logo", "open.gif", "|", "<"):
        assert leaked not in body, leaked


def test_receipt_keeps_data_table(converted):
    body = converted("html")["Receipt with a data table"]["body"]
    assert "| Item | Qty | Price |" in body and "| Widget | 2 | $10.00 |" in body
    assert "Total: $24.50" in body


def test_html_edge_cases(converted):
    c = converted("html")
    assert (c.stats.ok, c.stats.body_html) == (6, 6)
    assert "The body lives inside an unclosed head." in c["Unclosed head"]["body"]
    assert "Secure document notice" not in c["Unclosed head"]["body"]
    ws = c["Whitespace and structure"]["body"]
    assert "Spaced out words" in ws and "line one  \nline two" in ws and "keep    this    spacing" in ws
    assert "trackOpen" not in ws and "* first item" in ws
    assert "An XHTML message." in c["XHTML"]["body"]
    assert "The real content is in the HTML part." in c["Empty plain part"]["body"]


@pytest.mark.xfail(strict=True, reason="TODO: hidden preheader text (display:none) is kept")
def test_hidden_preheader_is_dropped(converted):
    assert "Hidden preheader text" not in converted("html")["Layout newsletter"]["body"]


@pytest.mark.xfail(strict=True, reason="TODO bug: relative links from email bodies point nowhere in the output")
def test_relative_links_are_not_kept(converted):
    assert "](../docs/template.md)" not in converted("html")["Layout newsletter"]["body"]


# --- encodings -------------------------------------------------------------------------------------------------


def test_encodings(converted):
    c = converted("encodings")
    assert (c.stats.ok, c.stats.errors) == (11, 0)
    assert c["Grüße und Küsse 🎉"]["fm"]["from"] == "Zoë Exämple <zoe@example.com>"
    assert c["Grüße und Küsse 🎉"]["path"].name.startswith("2024-01-15-1030-grue-und-kusse-")
    assert "-no-subject-" in c["日本語の件名"]["path"].name
    assert "Café crème brûlée." in c["Latin-1 quoted-printable"]["body"]
    assert c["Charset unknown-8bit"]["body"].startswith("\n# Charset unknown-8bit\n\ncaf")
    assert "with a raw high byte" in c["Charset x-made-up-charset"]["body"]
    assert "A base64 encoded body ✓" in c["Base64 encoded subject ✓"]["body"]
    assert "This message uses CRLF line endings." in c["CRLF message in an LF file"]["body"]
    assert "From and To are malformed." in c["Malformed address headers"]["body"]


@pytest.mark.xfail(strict=True, reason="TODO bug: a malformed From header is parsed to '<>' and the sender is lost")
def test_malformed_from_header_keeps_raw_value(converted):
    assert "broken" in converted("encodings")["Malformed address headers"]["fm"]["from"]


def test_missing_and_bad_dates_go_to_undated(converted):
    c = converted("encodings")
    assert c["Unparseable date"]["path"].parent.name == "undated"
    assert c["Unparseable date"]["fm"]["date"] is None
    nameless = [m for s, m in c.messages.items() if s == ""]
    assert len(nameless) == 1 and nameless[0]["path"].parent.name == "undated"
    assert "# (no subject)" in nameless[0]["text"]


def test_dates_use_the_senders_timezone(converted):
    # Documents current behavior; see the timezone item in TODO.md.
    m = converted("encodings")["Sent from UTC-8 late at night"]
    assert m["fm"]["date"] == "2024-01-31T23:45:00-08:00"
    assert m["path"].parent.relative_to(m["path"].parents[2]) == Path("2024/01")


# --- mbox structure --------------------------------------------------------------------------------------------


def test_separator_lookalikes_do_not_split_messages(converted):
    c = converted("separators")
    assert c.stats.ok == 4
    assert "From here on, lines start with From.\nFrom me, too." in c["From line in body"]["body"]
    assert "@xxx Mon Jan 15" in c["Separator-like line without blank line before it"]["body"]


@pytest.mark.xfail(strict=True, reason="TODO: mboxrd '>From ' lines are not unescaped")
def test_mboxrd_from_lines_are_unescaped(converted):
    assert "\nFrom the archive" in converted("separators")["Escaped From line"]["body"]


def test_crlf_mbox(converted):
    c = converted("crlf")
    assert (c.stats.ok, c.stats.errors) == (2, 0)
    assert "First CRLF message." in c["CRLF one"]["body"]


def test_empty_mbox(converted):
    c = converted("empty")
    assert (c.stats.ok, c.stats.processed) == (0, 0)
    assert (c.out / "messages.jsonl").read_text() == ""


def test_zst_input(tmp_path):
    zstd = pytest.importorskip("shutil").which("zstd")
    if not zstd:
        pytest.skip("zstd CLI not installed")
    import subprocess

    src = tmp_path / "basic.mbox.zst"
    subprocess.run([zstd, "-q", "--long=27", str(FIXTURES / "basic.mbox"), "-o", str(src)], check=True)
    assert convert(src, tmp_path / "out", ConvertOptions(workers=1)).ok == 8
