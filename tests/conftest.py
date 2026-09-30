"""Builders for fabricated test messages. Never put real email in this folder."""

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

SEP = b"From 1700000000000000000@xxx Tue Nov 14 22:13:20 +0000 2023\n"


def make_message(
    subject="Hello there",
    body="Just checking in.",
    html=None,
    labels="Inbox",
    message_id="<abc123@example.test>",
    date=datetime(2023, 11, 14, 22, 13, tzinfo=UTC),
    attachments=(),
) -> bytes:
    """attachments: iterable of (filename, content_type, bytes)."""
    m = EmailMessage()
    m["From"] = "Alice Example <alice@example.test>"
    m["To"] = "Bob Example <bob@example.test>, carol@example.test"
    if subject is not None:
        m["Subject"] = subject
    if message_id is not None:
        m["Message-ID"] = message_id
    if date is not None:
        m["Date"] = format_datetime(date)
    if labels is not None:
        m["X-Gmail-Labels"] = labels
    m["X-GM-THRID"] = "1780000000000000000"
    if body is not None:
        m.set_content(body)
    if html is not None:
        if body is None:
            m.set_content(html, subtype="html")
        else:
            m.add_alternative(html, subtype="html")
    for name, ctype, data in attachments:
        maintype, subtype = ctype.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes()


def make_mbox(*messages: bytes) -> bytes:
    out = b""
    for raw in messages:
        out += SEP + raw.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n\n"
    return out


@pytest.fixture
def mbox_file(tmp_path):
    def write(*messages: bytes):
        p = tmp_path / "test.mbox"
        p.write_bytes(make_mbox(*messages))
        return p

    return write
