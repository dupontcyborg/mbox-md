import json

from conftest import make_message

from mbox_md._attachments import StoredAttachment
from mbox_md._parse import parse_message
from mbox_md._render import render_markdown


def front_matter(text: str) -> dict:
    block = text.split("---\n")[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in block.strip().splitlines())}


def test_front_matter_is_json_valued_yaml():
    msg = parse_message(make_message(subject='Quotes " and: colons'))
    fm = front_matter(render_markdown(msg, [], depth=2))
    assert fm["subject"] == 'Quotes " and: colons'
    assert fm["to"] == ["bob@example.test", "carol@example.test"]
    assert fm["date"] == "2023-11-14T22:13:00+00:00"
    assert fm["attachments"] == []


def test_heading_and_empty_body_placeholders():
    text = render_markdown(parse_message(make_message(subject=None, body="")), [], depth=2)
    assert "# (no subject)" in text and "_(no body)_" in text


def test_attachment_links_climb_to_output_root():
    att = StoredAttachment("ab12", "attachments/ab/ab12.pdf", "My File.pdf", 1234, "application/pdf")
    text = render_markdown(parse_message(make_message()), [att], depth=2)
    assert "- [My File.pdf](../../attachments/ab/ab12.pdf) (1,234 bytes)" in text
