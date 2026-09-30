from conftest import make_message

from mbox_md import ConvertOptions, parse_message

PNG = b"\x89PNG\r\n\x1a\n"


def test_plain_message_fields():
    msg = parse_message(make_message(labels="Inbox,Important, Opened"))
    assert msg is not None
    assert msg.subject == "Hello there"
    assert msg.message_id == "<abc123@example.test>"
    assert msg.to == ["bob@example.test", "carol@example.test"]
    assert msg.labels == ["Inbox", "Important", "Opened"]
    assert msg.thread_id == "1780000000000000000"
    assert msg.date is not None and msg.date.year == 2023
    assert msg.body == "Just checking in."
    assert msg.body_kind == "plain"


def test_html_only_message_becomes_markdown():
    msg = parse_message(make_message(body=None, html="<p>Hi <b>Bob</b></p><ul><li>one</li></ul>"))
    assert msg.body_kind == "html"
    assert "**Bob**" in msg.body and "one" in msg.body


def test_multipart_alternative_prefers_plain():
    msg = parse_message(make_message(body="plain version", html="<p>html version</p>"))
    assert msg.body_kind == "plain" and msg.body == "plain version"


def test_empty_plain_falls_back_to_html():
    msg = parse_message(make_message(body="   ", html="<p>real body</p>"))
    assert msg.body_kind == "html" and "real body" in msg.body


def test_missing_subject_and_date():
    msg = parse_message(make_message(subject=None, date=None))
    assert msg.subject == "" and msg.date is None


def test_spam_and_trash_are_skipped_by_default():
    assert parse_message(make_message(labels="Spam")) is None
    assert parse_message(make_message(labels="Inbox,Trash")) is None


def test_skip_labels_can_be_emptied():
    assert parse_message(make_message(labels="Spam"), ConvertOptions(skip_labels=frozenset())) is not None


def test_attachments_are_collected():
    msg = parse_message(make_message(attachments=[("notes.txt", "text/plain", b"attached notes")]))
    assert [(a.name, a.ext, a.data) for a in msg.attachments] == [("notes.txt", ".txt", b"attached notes")]


def test_small_inline_images_are_dropped_but_large_ones_kept():
    small = PNG + b"x" * 100
    large = PNG + b"x" * 10_000
    raw = make_message()
    # Inline (no Content-Disposition: attachment) images, as signatures and tracking pixels are sent.
    from email import message_from_bytes, policy

    m = message_from_bytes(raw, policy=policy.default)
    m.make_mixed()
    for data in (small, large):
        m.add_attachment(data, maintype="image", subtype="png", disposition="inline")
    msg = parse_message(m.as_bytes())
    assert [len(a.data) for a in msg.attachments] == [len(large)]


def test_unknown_charset_does_not_crash():
    raw = (
        b"Subject: odd charset\nMessage-ID: <cs@example.test>\nContent-Type: text/plain; charset=unknown-8bit\n\n"
        b"caf\xe9\n"
    )
    msg = parse_message(raw)
    assert msg is not None and msg.body.startswith("caf")


def test_html_drops_head_scripts_styles_and_images():
    html = (
        "<html><head><title>Tab title</title><style>p{color:red}</style></head>"
        '<body><script>track()</script><p>Visible <img src="logo.png" alt="Logo"> text</p></body></html>'
    )
    msg = parse_message(make_message(body=None, html=html))
    assert "Visible" in msg.body and "text" in msg.body
    for leaked in ("Tab title", "color:red", "track()", "logo.png", "Logo"):
        assert leaked not in msg.body


def test_html_links_and_lists_survive():
    html = '<p>See <a href="https://example.test/doc">the doc</a></p><ul><li>one</li><li>two</li></ul>'
    msg = parse_message(make_message(body=None, html=html))
    assert "[the doc](https://example.test/doc)" in msg.body
    assert "* one" in msg.body and "* two" in msg.body


def test_html_nbsp_padding_is_collapsed_but_structure_kept():
    html = (
        "<p>Hello&nbsp;&nbsp;&nbsp;&nbsp;world&nbsp;&nbsp;</p><p>&nbsp;&nbsp;</p>"
        "<p>line one<br>line two</p><pre><code>keep    this    spacing</code></pre>"
    )
    body = parse_message(make_message(body=None, html=html)).body
    assert "Hello world" in body
    assert "line one  \nline two" in body
    assert "keep    this    spacing" in body
    assert not any(line.strip() == "" and line for line in body.split("\n"))


def test_layout_tables_become_paragraphs():
    html = (
        '<table role="presentation"><tr><td><p>First paragraph.</p><p>Second paragraph.</p></td></tr>'
        "<tr><td><table><tr><td>Nested footer</td></tr></table></td></tr></table>"
    )
    body = parse_message(make_message(body=None, html=html)).body
    assert "|" not in body
    assert "First paragraph.\n\nSecond paragraph." in body
    assert "Nested footer" in body


def test_small_data_tables_stay_tables():
    html = "<table><tr><th>Item</th><th>Price</th></tr><tr><td>Widget</td><td>$5</td></tr></table>"
    body = parse_message(make_message(body=None, html=html)).body
    assert "| Item | Price |" in body and "| Widget | $5 |" in body


def test_unclosed_head_does_not_swallow_body():
    html = "<html><head><meta charset='UTF-8'><title>Tab title</title><body><p>The actual message.</p></body></html>"
    body = parse_message(make_message(body=None, html=html)).body
    assert "The actual message." in body and "Tab title" not in body


def test_dead_links_are_unwrapped_and_protocol_relative_links_fixed():
    html = (
        '<p><a href="../docs/x.md">relative</a> <a href="#top">fragment</a> <a href="javascript:void(0)">js</a> '
        '<a href="//example.com/a">protocol-relative</a> <a href="mailto:a@example.com">mail</a></p>'
    )
    body = parse_message(make_message(body=None, html=html)).body
    assert body.startswith("relative fragment js [protocol-relative](https://example.com/a)")
    assert "[mail](mailto:a@example.com)" in body


def test_malformed_from_falls_back_to_raw_header():
    raw = b"From: <<broken@@example.com\nMessage-ID: <m@example.test>\nSubject: x\n\nbody\n"
    assert parse_message(raw).from_ == "<<broken@@example.com"


def test_well_formed_from_is_unchanged():
    assert parse_message(make_message()).from_ == "Alice Example <alice@example.test>"


def test_strip_quotes_handles_wrapped_attribution_and_outlook_tails():
    from mbox_md._body import strip_quoted_replies

    wrapped = "Yes.\n\nOn Mon, Jan 15, 2024 at 10:00 AM Bob Example <\nbob@example.org> wrote:\n\n> hi\n\nThanks"
    assert strip_quoted_replies(wrapped) == "Yes.\n\nThanks"
    assert strip_quoted_replies("Reply\n\n-----Original Message-----\nFrom: x\nstuff") == "Reply"
    assert strip_quoted_replies("Reply\n\nFrom: Bob\nSent: Monday\nTo: Alice\n\nold text") == "Reply"
    prose = "Top line\nOn the other hand, this is prose.\n\nNo quotes here."
    assert strip_quoted_replies(prose) == prose
    assert strip_quoted_replies("Inline > arrow stays\n>quoted") == "Inline > arrow stays"
