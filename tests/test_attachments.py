import pytest

from mbox_md.attachments import AttachmentStore, ext_for


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


@pytest.mark.xfail(strict=True, reason="TODO bug: same bytes with different extensions are stored twice")
def test_store_dedupes_same_content_with_different_extensions(tmp_path):
    store = AttachmentStore(tmp_path)
    store.put(b"same bytes", ".txt")
    store.put(b"same bytes", ".dat")
    assert len([p for p in (tmp_path / "attachments").rglob("*") if p.is_file()]) == 1


@pytest.mark.xfail(strict=True, reason="TODO bug: ext_for accepts meaningless hex suffixes as extensions")
def test_ext_for_ignores_hex_junk_suffix():
    assert ext_for("report.e3fc6c20", "application/pdf") == ".pdf"
