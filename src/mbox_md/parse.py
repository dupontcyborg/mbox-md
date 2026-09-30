"""Turn raw message bytes into a `ParsedMessage`. No file I/O."""

import email
import email.policy
import email.utils
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage, Message

from .attachments import ext_for
from .body import BodyKind, extract_body
from .naming import safe_name
from .options import ConvertOptions
from .reader import message_hash


@dataclass(frozen=True)
class AttachmentPart:
    name: str
    ext: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ParsedMessage:
    key: str
    """Hex id derived from the Message-ID (or the headers when there is none)."""
    message_id: str
    date: datetime | None
    from_: str
    to: list[str]
    cc: list[str]
    subject: str
    labels: list[str]
    thread_id: str | None
    body: str
    body_kind: BodyKind | None
    attachments: list[AttachmentPart]


def norm_labels(m: EmailMessage) -> list[str]:
    raw = re.sub(r"\s+", " ", str(m.get("X-Gmail-Labels") or ""))
    return [label.strip() for label in raw.split(",") if label.strip()]


def header_text(m: EmailMessage, header: str) -> str:
    """The parsed header, or its raw unfolded value when parsing lost the address (e.g. `<<a@@b.com` -> `<>`)."""
    v = m.get(header)
    if v is None:
        return ""
    text = str(v)
    if getattr(v, "defects", None) and "@" not in text:
        for name, raw in m.raw_items():
            if name.lower() == header.lower():
                return re.sub(r"\s*\r?\n\s*", " ", str(raw)).strip()
    return text


def addr_list(m: EmailMessage, header: str) -> list[str]:
    v = m.get(header)
    return [a for _, a in email.utils.getaddresses([str(v)])] if v else []


def parse_date(m: EmailMessage) -> datetime | None:
    try:
        return email.utils.parsedate_to_datetime(str(m["Date"]))
    except Exception:
        return None


def parse_message(raw: bytes, options: ConvertOptions | None = None) -> ParsedMessage | None:
    """Parse one raw message. Returns None when it carries a label in `options.skip_labels`."""
    options = options or ConvertOptions()
    m = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(m, EmailMessage)
    labels = norm_labels(m)
    if options.skip_labels & set(labels):
        return None
    date = parse_date(m)
    subject = str(m["Subject"] or "")
    body, body_kind, body_part = extract_body(m, options.max_html)
    return ParsedMessage(
        key=message_hash(raw),
        message_id=str(m["Message-ID"] or "").strip(),
        date=date,
        from_=header_text(m, "From"),
        to=addr_list(m, "To"),
        cc=addr_list(m, "Cc"),
        subject=subject,
        labels=labels,
        thread_id=str(m["X-GM-THRID"] or "") or None,
        body=body,
        body_kind=body_kind,
        attachments=list(iter_attachments(m, body_part, options)),
    )


def leaf_parts(part: Message) -> Iterator[Message]:
    """Non-multipart parts, depth first. An attached message (message/rfc822) counts as one leaf."""
    if part.get_content_type() == "message/rfc822" or not part.is_multipart():
        yield part
        return
    for sub in part.get_payload():
        yield from leaf_parts(sub)


def iter_attachments(m: EmailMessage, body_part: Message | None, options: ConvertOptions) -> Iterator[AttachmentPart]:
    for p in leaf_parts(m):
        if p is body_part:
            continue
        ct = p.get_content_type()
        fn = p.get_filename()
        disp = p.get_content_disposition()
        if ct in ("text/plain", "text/html") and not fn and disp != "attachment":
            continue
        if ct == "message/rfc822":
            yield from forwarded_message(p, fn)
            continue
        data = p.get_payload(decode=True)
        if not data:
            continue
        if ct.startswith("image/") and len(data) < options.min_inline_image and disp != "attachment":
            continue
        ext = ext_for(str(fn) if fn else "", ct, data)
        name = safe_name(str(fn)) if fn else "attachment" + ext
        yield AttachmentPart(name=name, ext=ext, content_type=ct, data=data)


def forwarded_message(part: Message, filename: str | None) -> Iterator[AttachmentPart]:
    """An attached email, saved as a standalone .eml named after its subject."""
    payload = part.get_payload()
    if not payload:
        return
    inner = payload[0]
    data = inner.as_bytes(policy=email.policy.compat32)
    subject = str(inner["Subject"] or "").strip()
    name = safe_name(str(filename)) if filename else safe_name(subject or "forwarded-message") + ".eml"
    yield AttachmentPart(name=name, ext=".eml", content_type="message/rfc822", data=data)
