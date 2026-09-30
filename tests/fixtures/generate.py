"""Generate the committed .mbox test fixtures. Every message here is fabricated.

Run from the repo root after changing this file:

    uv run python tests/fixtures/generate.py

`tests/test_fixtures.py` regenerates everything and fails if the committed files differ, so the fixtures can
only ever contain what this script writes. Addresses use reserved domains only (RFC 2606 / RFC 6761).
"""

import base64
import sys
from datetime import UTC, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

HERE = Path(__file__).parent
BASE_DATE = datetime(2024, 1, 15, 9, 30, tzinfo=UTC)
SMTP = policy.SMTP.clone(linesep="\n")  # mbox files use LF; crlf.mbox converts explicitly

ALICE = "Alice Example <alice@example.com>"
BOB = "Bob Example <bob@example.org>"
CAROL = "carol@example.net"


# --- deterministic fake payloads -------------------------------------------------------------------------------


def fake_bytes(seed: str, size: int) -> bytes:
    """Deterministic filler bytes, so fixtures are identical on every run."""
    out, block = bytearray(), seed.encode()
    while len(out) < size:
        block = base64.b64encode(block)[:64]
        out += block
    return bytes(out[:size])


def fake_pdf(seed: str, size: int = 2048) -> bytes:
    return b"%PDF-1.4\n% fabricated test file\n" + fake_bytes(seed, size)


def fake_png(seed: str, size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + fake_bytes(seed, size - 8)


# --- message builders ------------------------------------------------------------------------------------------


class Mbox:
    def __init__(self, name: str):
        self.name = name
        self.messages: list[tuple[datetime, bytes]] = []

    def add(self, raw: bytes, date: datetime = BASE_DATE) -> None:
        self.messages.append((date, raw))

    def email(self, n: int, *, date: datetime | None | str = "auto", **kw) -> None:
        """Message number n. By default it's dated BASE_DATE + n hours; pass a datetime, or None for no Date."""
        when = BASE_DATE + timedelta(hours=n) if date == "auto" else date
        msg = build(f"<{self.name}.{n}@fixtures.example.com>", date=when, **kw)
        self.add(finish(msg), when or BASE_DATE)

    def to_bytes(self) -> bytes:
        out = bytearray()
        for i, (date, raw) in enumerate(self.messages):
            sep = f"From {1780000000000000000 + i}@xxx {date.strftime('%a %b %d %H:%M:%S %z %Y')}\n"
            out += sep.encode() + raw.rstrip(b"\r\n") + b"\n\n"
        return bytes(out)


def build(
    message_id: str | None,
    *,
    subject: str | None = "Fixture message",
    date: datetime | None = BASE_DATE,
    labels: str | None = "Inbox",
    sender: str = ALICE,
    to: str = f"{BOB}, {CAROL}",
    cc: str | None = None,
    body: str | None = "Plain text body.",
    html: str | None = None,
    attachments: tuple = (),
    thread: str = "1790000000000000001",
) -> EmailMessage:
    m = EmailMessage(policy=SMTP)
    m["From"] = sender
    m["To"] = to
    if cc:
        m["Cc"] = cc
    if subject is not None:
        m["Subject"] = subject
    if date is not None:
        m["Date"] = format_datetime(date)
    if message_id is not None:
        m["Message-ID"] = message_id
    if labels is not None:
        m["X-Gmail-Labels"] = labels
    m["X-GM-THRID"] = thread
    if body is not None:
        m.set_content(body)
    if html is not None:
        if body is None:
            m.set_content(html, subtype="html")
        else:
            m.add_alternative(html, subtype="html")
    for att in attachments:
        name, ctype, data, *rest = att
        disposition = rest[0] if rest else "attachment"
        maintype, subtype = ctype.split("/")
        if not m.is_multipart():
            m.make_mixed()
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name, disposition=disposition)
    return m


def finish(m: EmailMessage) -> bytes:
    """Replace the random MIME boundaries with fixed ones, then serialize."""
    for i, part in enumerate(p for p in m.walk() if p.is_multipart()):
        part.set_boundary(f"===============fixture-boundary-{i}==")
    return m.as_bytes(policy=SMTP)


def raw(*lines: str) -> bytes:
    """A hand-written message, for malformed input the email package would refuse to produce."""
    return ("\n".join(lines) + "\n").encode("latin-1")


# --- fixtures --------------------------------------------------------------------------------------------------


def basic() -> Mbox:
    """One of each ordinary shape."""
    mb = Mbox("basic")
    mb.email(1, subject="Plain text only", body="Hello Bob,\n\nThis is a plain text message.\n\nAlice")
    mb.email(2, subject="HTML only", body=None, html="<p>Hello <b>Bob</b>, this one is <i>HTML only</i>.</p>")
    mb.email(3, subject="Multipart alternative", body="The plain version.", html="<p>The HTML version.</p>")
    mb.email(4, subject="Re: Fwd: RE: Quarterly plan (v2)!", body="Reply prefixes are stripped from the slug.")
    mb.email(5, subject="Café résumé naïve", body="Accented subject for slug transliteration.")
    mb.email(6, subject="Weekly sync " * 12, body="A very long subject that must be truncated.")
    mb.email(7, subject="With Cc", cc="Dave Example <dave@example.com>", body="Has a Cc header.")
    mb.email(8, subject="Reply quoting", body="Sounds good.\n\nOn Monday, Bob wrote:\n> Shall we meet?\n> -- Bob")
    return mb


def labels() -> Mbox:
    """Gmail label handling and Message-ID dedup."""
    mb = Mbox("labels")
    mb.email(1, subject="In the inbox", labels="Inbox,Important,Opened")
    mb.email(2, subject="Spam message", labels="Spam")
    mb.email(3, subject="Trashed message", labels="Opened,Trash")
    mb.add(
        raw(
            "From: alice@example.com",
            "To: bob@example.org",
            "Subject: Folded labels",
            "Date: Mon, 15 Jan 2024 13:30:00 +0000",
            "Message-ID: <labels.4@fixtures.example.com>",
            "X-Gmail-Labels: Inbox,",
            " Category Updates,",
            "\tOpened",
            "",
            "The labels header is folded across three lines.",
        )
    )
    mb.email(5, subject="No labels header", labels=None)
    mb.email(6, subject="Archived, custom label", labels="Archived,Receipts/2024")
    first = build("<labels.dup@fixtures.example.com>", subject="Duplicate, first copy", labels="Inbox")
    second = build("<labels.dup@fixtures.example.com>", subject="Duplicate, second copy", labels="Sent")
    mb.add(finish(first))
    mb.add(finish(second))
    # The same message twice, first as a Spam copy: the Inbox copy must still be converted.
    spam = build("<labels.spamfirst@fixtures.example.com>", subject="Spam copy first", labels="Spam")
    inbox = build("<labels.spamfirst@fixtures.example.com>", subject="Spam copy first", labels="Inbox,Starred")
    mb.add(finish(spam))
    mb.add(finish(inbox))
    mb.add(finish(build(None, subject="No Message-ID one", body="First message without a Message-ID.")))
    mb.add(finish(build(None, subject="No Message-ID two", body="Second message without a Message-ID.")))
    return mb


def attachments() -> Mbox:
    """Attachment storage, dedup, extension detection, and inline image filtering."""
    mb = Mbox("attachments")
    report = fake_pdf("report")
    mb.email(1, subject="Report attached", attachments=[("report.pdf", "application/pdf", report)])
    mb.email(2, subject="Same bytes, new name", attachments=[("copy-of-report.pdf", "application/pdf", report)])
    mb.email(3, subject="Same bytes, junk extension", attachments=[("report.e3fc6c20", "application/pdf", report)])
    mb.email(4, subject="Same name, new bytes", attachments=[("report.pdf", "application/pdf", fake_pdf("v2"))])
    mb.email(
        5,
        subject="Inline images",
        body=None,
        html="<p>Logo and photo inline.</p>",
        attachments=[
            ("pixel.png", "image/png", fake_png("pixel", 300), "inline"),
            ("photo.png", "image/png", fake_png("photo", 12_000), "inline"),
            ("tiny-but-attached.png", "image/png", fake_png("tiny", 200), "attachment"),
        ],
    )
    mb.email(6, subject="Unnamed octet-stream", attachments=[(None, "application/octet-stream", fake_pdf("anon"))])
    mb.email(7, subject="Path in filename", attachments=[("../../etc/passwd", "text/plain", b"not a real passwd\n")])
    mb.email(8, subject="Attachment only", body=None, attachments=[("notes.txt", "text/plain", b"Just a file.\n")])
    mb.email(9, subject="Calendar invite", attachments=[("invite.ics", "text/calendar", ics())])
    forwarded = build("<attachments.inner@fixtures.example.com>", subject="The original", body="Forwarded body.")
    outer = build("<attachments.10@fixtures.example.com>", subject="Fwd: The original", body="See below.")
    outer.make_mixed()
    outer.add_attachment(forwarded)
    mb.add(finish(outer))
    return mb


def ics() -> bytes:
    return (
        b"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//mbox-md//fixtures//EN\nBEGIN:VEVENT\nUID:fixture-1@example.com\n"
        b"DTSTART:20240120T150000Z\nDTEND:20240120T160000Z\nSUMMARY:Fixture planning\nEND:VEVENT\nEND:VCALENDAR\n"
    )


LAYOUT_EMAIL = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Newsletter tab title</title>
<style>.btn { color: #fff; } @media (max-width: 600px) { .col { width: 100% } }</style></head>
<body><div style="display:none;max-height:0">Hidden preheader text</div>
<table role="presentation" width="100%"><tr><td>
  <table><tr><td><img src="https://example.com/logo.png" alt="Example Co logo" width="120"></td></tr></table>
  <h1>Monthly update</h1>
  <p>First paragraph of the newsletter.</p><p>Second paragraph, with a <a href="https://example.com/post">link</a>.</p>
  <p><a href="https://example.com/track?id=1"><img src="https://example.com/banner.png"></a></p>
  <p>See <a href="../docs/template.md">the template</a> in the repo.</p>
  <table><tr><td>Nested footer text</td><td><a href="https://example.com/unsubscribe">Unsubscribe</a></td></tr></table>
</td></tr></table><img src="https://example.com/open.gif" width="1" height="1"></body></html>"""

RECEIPT_EMAIL = """<html><body><p>Thanks for your order.</p>
<table><tr><th>Item</th><th>Qty</th><th>Price</th></tr>
<tr><td>Widget</td><td>2</td><td>$10.00</td></tr><tr><td>Gadget</td><td>1</td><td>$4.50</td></tr></table>
<p>Total: $24.50</p></body></html>"""

UNCLOSED_HEAD_EMAIL = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Secure document notice</title><body><p>The body lives inside an unclosed head.</p></body></html>"""

WHITESPACE_EMAIL = """<html><body><script>trackOpen()</script>
<p>Spaced&nbsp;&nbsp;&nbsp;&nbsp;out&nbsp;&nbsp;words</p><p>&nbsp;&nbsp;</p>
<p>line one<br>line two</p><pre><code>keep    this    spacing</code></pre>
<ul><li>first item</li><li>second item</li></ul></body></html>"""

XHTML_EMAIL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>An XHTML message.</p></body></html>"""


def html() -> Mbox:
    """HTML-to-Markdown conversion on the shapes real mail uses."""
    mb = Mbox("html")
    mb.email(1, subject="Layout newsletter", body=None, html=LAYOUT_EMAIL)
    mb.email(2, subject="Receipt with a data table", body=None, html=RECEIPT_EMAIL)
    mb.email(3, subject="Unclosed head", body=None, html=UNCLOSED_HEAD_EMAIL)
    mb.email(4, subject="Whitespace and structure", body=None, html=WHITESPACE_EMAIL)
    mb.email(5, subject="XHTML", body=None, html=XHTML_EMAIL)
    mb.email(6, subject="Empty plain part", body="   \n", html="<p>The real content is in the HTML part.</p>")
    return mb


def encodings() -> Mbox:
    """Charsets, encoded headers, and malformed headers."""
    mb = Mbox("encodings")
    mb.email(
        1, subject="Grüße und Küsse 🎉", sender="Zoë Exämple <zoe@example.com>", body="UTF-8 everywhere: ñ, ü, 😀."
    )
    mb.email(2, subject="日本語の件名", body="A subject with no Latin characters.")
    mb.add(
        raw(
            "From: latin1@example.com",
            "To: bob@example.org",
            "Subject: Latin-1 quoted-printable",
            "Date: Mon, 15 Jan 2024 12:00:00 +0000",
            "Message-ID: <encodings.3@fixtures.example.com>",
            "X-Gmail-Labels: Inbox",
            "MIME-Version: 1.0",
            "Content-Type: text/plain; charset=iso-8859-1",
            "Content-Transfer-Encoding: quoted-printable",
            "",
            "Caf=E9 cr=E8me br=FBl=E9e.",
        )
    )
    for n, charset in ((4, "unknown-8bit"), (5, "x-made-up-charset")):
        mb.add(
            raw(
                "From: odd@example.com",
                "To: bob@example.org",
                f"Subject: Charset {charset}",
                "Date: Mon, 15 Jan 2024 13:00:00 +0000",
                f"Message-ID: <encodings.{n}@fixtures.example.com>",
                "X-Gmail-Labels: Inbox",
                f"Content-Type: text/plain; charset={charset}",
                "Content-Transfer-Encoding: 8bit",
                "",
                "caf\xe9 with a raw high byte",
            )
        )
    mb.add(
        raw(
            "From: base64@example.com",
            "To: bob@example.org",
            "Subject: =?utf-8?b?QmFzZTY0IGVuY29kZWQgc3ViamVjdCDinJM=?=",
            "Date: Mon, 15 Jan 2024 14:00:00 +0000",
            "Message-ID: <encodings.6@fixtures.example.com>",
            "X-Gmail-Labels: Inbox",
            "Content-Type: text/plain; charset=utf-8",
            "Content-Transfer-Encoding: base64",
            "",
            base64.b64encode("A base64 encoded body ✓".encode()).decode(),
        )
    )
    mb.add(
        raw(
            "From: baddate@example.com",
            "To: undisclosed-recipients:;",
            "Subject: Unparseable date",
            "Date: sometime last tuesday",
            "Message-ID: <encodings.7@fixtures.example.com>",
            "X-Gmail-Labels: Inbox",
            "",
            "The Date header cannot be parsed.",
        )
    )
    mb.add(
        raw(
            "From: nothing@example.com",
            "To: bob@example.org",
            "Message-ID: <encodings.8@fixtures.example.com>",
            "X-Gmail-Labels: Inbox",
            "",
            "No Subject and no Date.",
        )
    )
    mb.add(
        raw(
            "From: <<broken@@example.com",
            "To: bob@example.org, , <>",
            "Subject: Malformed address headers",
            "Date: Mon, 15 Jan 2024 15:00:00 +0000",
            "Message-ID: <encodings.9@fixtures.example.com>",
            "X-Gmail-Labels: Inbox",
            "",
            "From and To are malformed.",
        )
    )
    tz = timezone(timedelta(hours=-8))
    mb.email(10, subject="Sent from UTC-8 late at night", date=datetime(2024, 1, 31, 23, 45, tzinfo=tz))
    mb.add(
        b"From: crlf@example.com\r\nTo: bob@example.org\r\nSubject: CRLF message in an LF file\r\n"
        b"Date: Mon, 15 Jan 2024 16:00:00 +0000\r\nMessage-ID: <encodings.11@fixtures.example.com>\r\n"
        b"X-Gmail-Labels: Inbox\r\n\r\nThis message uses CRLF line endings.\r\n"
    )
    return mb


def separators() -> Mbox:
    """Lines in bodies that look like mbox separators."""
    mb = Mbox("separators")
    mb.email(1, subject="From line in body", body="Hi,\n\nFrom here on, lines start with From.\nFrom me, too.\n")
    mb.email(2, subject="Escaped From line", body="Quoted:\n\n>From the archive, escaped by mboxrd.\n")
    fake_sep = "From 1780000000000000999@xxx Mon Jan 15 09:30:00 +0000 2024"
    mb.email(
        3, subject="Separator-like line without blank line before it", body=f"Line above.\n{fake_sep}\nLine below."
    )
    mb.email(4, subject="Last message", body="No trailing newline after this one.")
    return mb


def crlf() -> Mbox:
    """An entire mbox with CRLF line endings, as some Windows tools write them."""
    mb = Mbox("crlf")
    mb.email(1, subject="CRLF one", body="First CRLF message.")
    mb.email(2, subject="CRLF two", body="Second CRLF message.")
    return mb


def mailclient() -> Mbox:
    """A non-Gmail mbox, as mail clients write it: `From <sender> <asctime>` separators and mboxrd escaping."""
    lines = [
        "From alice@example.com Mon Jan  5 09:30:00 2024",
        "From: Alice Example <alice@example.com>",
        "To: bob@example.org",
        "Subject: Client-written message",
        "Date: Mon, 05 Jan 2024 09:30:00 +0000",
        "Message-ID: <mailclient.1@fixtures.example.com>",
        "",
        "Quoting the old archive:",
        "",
        ">From the escaped line, which should read 'From the escaped line'.",
        ">>From a doubly escaped line.",
        "",
        "From MAILER-DAEMON Fri Jul  8 12:08:34 2011",
        "From: Mail Delivery System <mailer-daemon@example.net>",
        "To: alice@example.com",
        "Subject: Undeliverable",
        "Date: Fri, 08 Jul 2011 12:08:34 +0000",
        "Message-ID: <mailclient.2@fixtures.example.com>",
        "",
        "Bounce body.",
        "",
        "From carol@example.net Thu Jan  1 00:00:00 UTC 1970",
        "From: carol@example.net",
        "Subject: Epoch, with a timezone name in the separator",
        "Date: Thu, 01 Jan 1970 00:00:00 +0000",
        "Message-ID: <mailclient.3@fixtures.example.com>",
        "",
        "Old.",
        "",
    ]
    mb = Mbox("mailclient")
    mb.raw_file = "\n".join(lines).encode()
    return mb


FIXTURES = {"basic": basic, "mailclient": mailclient, "labels": labels, "attachments": attachments, "html": html,
            "encodings": encodings, "separators": separators, "crlf": crlf}  # fmt: skip


def render_all() -> dict[str, bytes]:
    out = {}
    for name, fn in FIXTURES.items():
        mb = fn()
        out[f"{name}.mbox"] = getattr(mb, "raw_file", None) or mb.to_bytes()
    out["crlf.mbox"] = out["crlf.mbox"].replace(b"\n", b"\r\n")
    out["empty.mbox"] = b""
    return out


def main(outdir: Path = HERE) -> None:
    for name, data in render_all().items():
        (outdir / name).write_bytes(data)
        print(f"{name}: {len(data):,} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
