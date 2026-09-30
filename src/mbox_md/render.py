"""Render a parsed message as a Markdown document with YAML front matter. No file I/O."""

import json
from collections.abc import Sequence
from urllib.parse import quote

from .attachments import StoredAttachment
from .parse import ParsedMessage


def yaml_scalar(v: object) -> str:
    # JSON is valid YAML, and it escapes everything a hand-rolled YAML writer would get wrong.
    return json.dumps(v, ensure_ascii=False)


def render_markdown(msg: ParsedMessage, attachments: Sequence[StoredAttachment], depth: int) -> str:
    """`depth` is how many folders the .md file sits below the output root (2 for `YYYY/MM/`)."""
    lines = [
        "---",
        f"message_id: {yaml_scalar(msg.message_id)}",
        f"date: {yaml_scalar(msg.date.isoformat() if msg.date else None)}",
        f"from: {yaml_scalar(msg.from_)}",
        f"to: {yaml_scalar(msg.to)}",
        f"cc: {yaml_scalar(msg.cc)}",
        f"subject: {yaml_scalar(msg.subject)}",
        f"labels: {yaml_scalar(msg.labels)}",
        f"thread_id: {yaml_scalar(msg.thread_id)}",
        f"attachments: {yaml_scalar([a.path for a in attachments])}",
        "---",
        "",
        f"# {msg.subject or '(no subject)'}",
        "",
        msg.body or "_(no body)_",
    ]
    if attachments:
        lines += ["", "## Attachments", ""]
        for a in attachments:
            link = "../" * depth + quote(a.path)
            lines.append(f"- [{a.name}]({link}) ({a.size:,} bytes)")
    return "\n".join(lines) + "\n"
