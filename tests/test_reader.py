from conftest import SEP, make_mbox, make_message

from mbox_md._reader import iter_messages, message_key, split_chunks, split_messages, unescape_from_lines


def lines(data: bytes):
    return iter(data.splitlines(keepends=True))


def test_splits_on_gmail_separator(mbox_file):
    path = mbox_file(make_message(message_id="<a@x>"), make_message(message_id="<b@x>"))
    msgs = list(iter_messages(path))
    assert len(msgs) == 2
    assert b"<a@x>" in msgs[0] and b"<b@x>" in msgs[1]
    assert not msgs[0].startswith(b"From ")


def test_separator_needs_preceding_blank_line():
    data = SEP + b"Subject: x\n\nbody\n" + SEP + b"still body\n"
    assert len(list(split_messages(lines(data)))) == 1


def test_ordinary_from_line_in_body_is_not_a_separator():
    data = make_mbox(make_message(body="hi\n\nFrom me to you\nbye"))
    assert len(list(split_messages(lines(data)))) == 1


def test_message_key_uses_message_id():
    assert message_key(make_message(message_id="<k@example.test>")) == b"<k@example.test>"


def test_message_key_without_message_id_hashes_headers():
    a = make_message(message_id=None, subject="one")
    b = make_message(message_id=None, subject="two")
    assert message_key(a) != message_key(b)
    assert message_key(a) == message_key(a)


def test_message_key_ignores_body_for_crlf_mail():
    a = b"Subject: x\r\n\r\nbody one\r\n"
    b = b"Subject: x\r\n\r\nbody two\r\n"
    assert message_key(a) == message_key(b)


def test_non_separator_from_line_near_the_end_does_not_hide_later_separators():
    # A complete "From ..." body line close to the end of the data used to pause the scan for more input, which
    # never came, so the separators after it were missed.
    data = SEP + b"Subject: a\n\nFrom here on it's body text.\n\n" + SEP + b"Subject: b\n\nbody\n"
    expected = list(split_messages(lines(data)))
    assert len(expected) == 2
    assert list(split_chunks([data])) == expected
    assert list(split_chunks(data[i : i + 7] for i in range(0, len(data), 7))) == expected


def test_generic_mbox_separators():
    data = (
        b"From alice@example.com Mon Jan  5 09:30:00 2024\nSubject: a\n\nhi\n\n"
        b"From MAILER-DAEMON Fri Jul  8 12:08:34 2011\nSubject: b\n\nbounce\n\n"
        b"From bob@example.org Thu Jan  1 00:00:00 UTC 1970\nSubject: c\n\nold\n"
    )
    assert [m.split(b"\n", 1)[0] for m in split_chunks([data])] == [b"Subject: a", b"Subject: b", b"Subject: c"]


def test_unescape_from_lines():
    assert unescape_from_lines(b"a\n>From x\n>>From y\n> From z\n") == b"a\nFrom x\n>From y\n> From z\n"
