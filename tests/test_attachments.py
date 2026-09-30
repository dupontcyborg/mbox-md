from urllib.parse import quote

from mbox_md._attachments import AttachmentStore, StoredAttachment, ext_for
from mbox_md._writer import AttachmentIndex


def test_ext_for_prefers_filename_suffix():
    assert ext_for("Report.PDF", "application/octet-stream") == ".pdf"


def test_ext_for_falls_back_to_content_type():
    assert ext_for("", "image/png") == ".png"


def test_ext_for_sniffs_magic_bytes_for_octet_stream():
    assert ext_for("", "application/octet-stream", b"%PDF-1.7 ...") == ".pdf"
    assert ext_for("", "application/octet-stream", b"\x00\x01") == ".bin"


def test_store_writes_once_per_content(tmp_path):
    store = AttachmentStore(tmp_path)
    h1, rel1 = store.put(b"same bytes", ".txt")
    h2, rel2 = store.put(b"same bytes", ".txt")
    assert (h1, rel1) == (h2, rel2)
    assert (tmp_path / rel1).read_bytes() == b"same bytes"
    assert rel1.parts[:2] == ("attachments", h1[:2])


def test_store_keeps_different_content_apart(tmp_path):
    store = AttachmentStore(tmp_path)
    assert store.put(b"one", ".txt")[1] != store.put(b"two", ".txt")[1]


def test_ext_for_rejects_numeric_suffix_from_version_names():
    assert ext_for("release-v1.8", "application/pdf") == ".pdf"


def test_ext_for_keeps_short_unknown_but_plausible_suffixes():
    assert ext_for("design.sketch", "application/octet-stream") == ".sketch"


def test_index_consolidates_same_bytes_stored_under_two_extensions(tmp_path):
    store, index = AttachmentStore(tmp_path), AttachmentIndex()
    h, jpg_path = store.put(b"same bytes", ".jpg")
    _, jpeg_path = store.put(b"same bytes", ".jpeg")
    jpg, jpeg = jpg_path.as_posix(), jpeg_path.as_posix()
    for msg, rel, name in (("a.md", jpg, "photo.jpg"), ("b.md", jpeg, "photo.jpeg"), ("c.md", jpeg, "photo.jpeg")):
        (tmp_path / msg).write_text(f'attachments: ["{rel}"]\n- [{name}]({quote(rel)})\n', encoding="utf-8")
        index.add(msg, [StoredAttachment(h, rel, name, 10, "image/jpeg")])

    assert index.consolidate(tmp_path) == 1
    # Both are known extensions, so the one used by more references wins.
    assert index.entries[h].path == jpeg
    assert not (tmp_path / jpg).exists() and (tmp_path / jpeg).exists()
    for msg in ("a.md", "b.md", "c.md"):
        text = (tmp_path / msg).read_text(encoding="utf-8")
        assert jpeg in text and jpg not in text


def test_index_prefers_known_extension_over_popular_unknown_one(tmp_path):
    index = AttachmentIndex()
    refs = [("a.md", "attachments/ab/ab.weird"), ("b.md", "attachments/ab/ab.weird"), ("c.md", "attachments/ab/ab.pdf")]
    for msg, rel in refs:
        index.add(msg, [StoredAttachment("ab", rel, "x", 1, "application/pdf")])
    assert index.entries["ab"].path == "attachments/ab/ab.pdf"


def test_consolidation_never_corrupts_a_path_that_extends_the_old_one(tmp_path):
    index = AttachmentIndex()
    md = tmp_path / "m.md"
    md.write_text("[a](../attachments/ab/ab.htm) [b](../attachments/ab/ab.html)\n")
    for rel in ("attachments/ab/ab.html", "attachments/ab/ab.html", "attachments/ab/ab.htm"):
        index.add("m.md", [StoredAttachment("ab", rel, "x", 1, "text/html")])
    index.consolidate(tmp_path)
    assert md.read_text(encoding="utf-8") == "[a](../attachments/ab/ab.html) [b](../attachments/ab/ab.html)\n"


def test_ext_for_ignores_hex_junk_suffix():
    assert ext_for("report.e3fc6c20", "application/pdf") == ".pdf"


def test_ext_for_rejects_short_hex_ids():
    assert ext_for("report.a9e7c5", "application/pdf") == ".pdf"
    assert ext_for("song.mp3", "application/octet-stream") == ".mp3"
