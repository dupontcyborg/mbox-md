from conftest import SEP, make_mbox, make_message

from mbox_md.reader import iter_messages, message_key, split_messages


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
