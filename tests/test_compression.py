"""Compressed input and `compress_mbox`, all through compress-utils."""

import gzip
import hashlib
import shutil
import subprocess
from pathlib import Path

import compress_utils as cu
import pytest

from mbox_md import CompressionError, ReadError, compress_mbox
from mbox_md._cli import main
from mbox_md._compression import check_single_zstd_frame, detect
from mbox_md._reader import MboxStream, split_chunks, split_messages

FIXTURES = Path(__file__).parent / "fixtures"
ATTACHMENTS = (FIXTURES / "attachments.mbox").read_bytes()


def messages(path: Path) -> list[bytes]:
    with MboxStream(path) as s:
        return list(s)


def plain_messages() -> list[bytes]:
    return list(split_messages(iter(ATTACHMENTS.splitlines(keepends=True))))


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- reading ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("a.mbox.zst", lambda d: cu.compress(d, "zstd")),
        ("a.mbox.gz", lambda d: cu.compress(d, "gzip")),
        ("a.mbox.gz", gzip.compress),  # written by the stdlib, as `gzip` would
        ("misnamed.mbox", lambda d: cu.compress(d, "zstd")),  # detected by magic bytes, not the name
    ],
)
def test_compressed_input_matches_plain(tmp_path, name, make):
    assert messages(write(tmp_path, name, make(ATTACHMENTS))) == plain_messages()


def test_zstd_cli_output_is_readable(tmp_path):
    zstd = shutil.which("zstd")
    if not zstd:
        pytest.skip("zstd CLI not installed")
    src = tmp_path / "a.mbox.zst"
    # --long and checksums, like `zstd -T0 -10 --long=27` on a real archive.
    subprocess.run([zstd, "-q", "-T0", "--long=27", "--check", str(FIXTURES / "attachments.mbox"), "-o", str(src)])
    assert messages(src) == plain_messages()


def test_position_reaches_the_file_size(tmp_path):
    src = write(tmp_path, "a.mbox.zst", cu.compress(ATTACHMENTS, "zstd"))
    with MboxStream(src) as s:
        positions = [s.position for _ in s]
    assert positions == sorted(positions) and positions[-1] == src.stat().st_size


def test_stopping_early_is_quiet(tmp_path):
    src = write(tmp_path, "a.mbox.zst", cu.compress(ATTACHMENTS * 50, "zstd"))
    with MboxStream(src) as s:
        first = next(iter(s))
    assert b"Report attached" in first


def test_multi_frame_zstd_is_refused(tmp_path):
    src = write(tmp_path, "a.mbox.zst", cu.compress(ATTACHMENTS, "zstd") + cu.compress(b"more\n", "zstd"))
    with pytest.raises(ReadError, match="more than one zstd frame"):
        messages(src)


def test_multi_member_gzip_is_refused(tmp_path):
    src = write(tmp_path, "a.mbox.gz", gzip.compress(ATTACHMENTS) + gzip.compress(b"more\n"))
    with pytest.raises(ReadError, match="multi-member gzip"):
        messages(src)


@pytest.mark.parametrize(("name", "alg"), [("a.mbox.zst", "zstd"), ("a.mbox.gz", "gzip")])
def test_truncated_input_is_refused(tmp_path, name, alg):
    blob = cu.compress(ATTACHMENTS, alg)
    with pytest.raises(ReadError, match="truncated|decompress"):
        messages(write(tmp_path, name, blob[: len(blob) // 2]))


def test_garbage_after_magic_is_refused(tmp_path):
    with pytest.raises(ReadError):
        messages(write(tmp_path, "a.mbox.zst", b"\x28\xb5\x2f\xfd" + b"garbage" * 100))


def test_misnamed_plain_file_is_refused(tmp_path):
    with pytest.raises(ReadError, match="isn't zstd or gzip"):
        MboxStream(write(tmp_path, "a.mbox.zst", ATTACHMENTS))


def test_empty_file_is_plain(tmp_path):
    assert messages(write(tmp_path, "empty.mbox", b"")) == []


def test_frame_walker_accepts_single_frames_from_both_producers(tmp_path):
    check_single_zstd_frame(write(tmp_path, "cu.zst", cu.compress(ATTACHMENTS, "zstd")))
    check_single_zstd_frame(write(tmp_path, "tiny.zst", cu.compress(b"x", "zstd")))  # single-segment frame
    with pytest.raises(CompressionError, match="truncated"):
        check_single_zstd_frame(write(tmp_path, "cut.zst", cu.compress(ATTACHMENTS, "zstd")[:40]))


def test_split_chunks_matches_line_splitting_for_any_chunk_size():
    expected = plain_messages()
    for size in (1, 5, 127, 128, 4096, len(ATTACHMENTS)):
        assert list(split_chunks(ATTACHMENTS[i : i + size] for i in range(0, len(ATTACHMENTS), size))) == expected


# --- compress_mbox ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("alg", "ext", "reader"), [("zstd", ".zst", None), ("gzip", ".gz", gzip.decompress)])
def test_compress_mbox_round_trips_and_verifies(tmp_path, alg, ext, reader):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)
    result = compress_mbox(src, algorithm=alg, level=3)
    assert result.dest == tmp_path / f"a.mbox{ext}" and src.exists() and not result.source_deleted
    assert result.sha256 == hashlib.sha256(ATTACHMENTS).hexdigest() and result.ratio > 1
    assert detect(result.dest) == alg and messages(result.dest) == plain_messages()
    if reader:  # readable by standard tools, not just compress-utils
        assert reader(result.dest.read_bytes()) == ATTACHMENTS
    assert not list(tmp_path.glob(".*.tmp"))


def test_compress_mbox_output_is_readable_by_zstd_cli(tmp_path):
    zstd = shutil.which("zstd")
    if not zstd:
        pytest.skip("zstd CLI not installed")
    dest = compress_mbox(write(tmp_path, "a.mbox", ATTACHMENTS)).dest
    assert subprocess.run([zstd, "-dcq", str(dest)], capture_output=True, check=True).stdout == ATTACHMENTS


def test_compress_mbox_deletes_only_when_asked(tmp_path):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)
    result = compress_mbox(src, delete_source=True)
    assert result.source_deleted and not src.exists() and messages(result.dest) == plain_messages()


def test_failed_verification_keeps_the_original_and_removes_the_output(tmp_path, monkeypatch):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)

    real = cu.DecompressStream

    class Corrupting:
        def __init__(self, alg):
            self.d = real(alg)

        def decompress(self, data):
            return self.d.decompress(data)[:-1]

        def finish(self):
            return self.d.finish()

    monkeypatch.setattr("mbox_md._compression.cu.DecompressStream", Corrupting)
    with pytest.raises(CompressionError, match="verification failed"):
        compress_mbox(src, delete_source=True)
    assert src.read_bytes() == ATTACHMENTS
    assert not (tmp_path / "a.mbox.zst").exists() and not list(tmp_path.glob(".*.tmp"))


def test_compress_mbox_refuses_unsafe_requests(tmp_path):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)
    (tmp_path / "a.mbox.zst").write_bytes(b"existing")
    with pytest.raises(CompressionError, match="already exists"):
        compress_mbox(src)
    assert (tmp_path / "a.mbox.zst").read_bytes() == b"existing"
    compress_mbox(src, overwrite=True)
    with pytest.raises(CompressionError, match="already compressed"):
        compress_mbox(tmp_path / "a.mbox.zst", dest=tmp_path / "b.zst")
    with pytest.raises(CompressionError, match="level must be 1 to 10"):
        compress_mbox(src, dest=tmp_path / "c.zst", level=11)
    with pytest.raises(CompressionError, match="no such file"):
        compress_mbox(tmp_path / "missing.mbox")


# --- CLI -------------------------------------------------------------------------------------------------------


def test_cli_compress_source_and_delete(tmp_path, capsys):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)
    assert main([str(src), str(tmp_path / "out"), "--workers", "1", "--compress-source", "gzip"]) == 0
    assert src.exists() and gzip.decompress((tmp_path / "a.mbox.gz").read_bytes()) == ATTACHMENTS
    assert "verified; kept the original" in " ".join(capsys.readouterr().err.split())
    assert (
        main([str(src), str(tmp_path / "out2"), "-q", "--workers", "1", "--compress-source", "zstd", "--delete-source"])
        == 0
    )
    assert not src.exists() and messages(tmp_path / "a.mbox.zst") == plain_messages()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--delete-source"], "--delete-source needs --compress-source"),
        (["--compress-source", "zstd", "--dry-run"], "can't be combined with --dry-run"),
        (["--compress-source", "zstd", "--compress-level", "0"], "must be 1 to 10"),
    ],
)
def test_cli_compress_argument_errors(tmp_path, capsys, extra, message):
    with pytest.raises(SystemExit):
        main([str(FIXTURES / "basic.mbox"), str(tmp_path / "out"), *extra])
    assert message in capsys.readouterr().err


def test_cli_refuses_to_compress_compressed_input(tmp_path, capsys):
    src = write(tmp_path, "a.mbox.zst", cu.compress(ATTACHMENTS, "zstd"))
    with pytest.raises(SystemExit):
        main([str(src), str(tmp_path / "out"), "--compress-source", "gzip"])
    assert "already compressed" in capsys.readouterr().err


def test_cli_existing_output_is_a_clear_error(tmp_path, capsys):
    src = write(tmp_path, "a.mbox", ATTACHMENTS)
    (tmp_path / "a.mbox.zst").write_bytes(b"existing")
    assert main([str(src), str(tmp_path / "out"), "--workers", "1", "--compress-source", "zstd"]) == 1
    assert "already exists" in capsys.readouterr().err and src.exists()
