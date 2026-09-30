"""Compressed input and output through compress-utils: read `.mbox.zst` / `.mbox.gz`, and compress an mbox with
verification before anything is deleted."""

import hashlib
import os
import queue
import struct
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import compress_utils as cu

Algorithm = Literal["zstd", "gzip"]
EXTENSIONS: dict[Algorithm, str] = {"zstd": ".zst", "gzip": ".gz"}
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"
CHUNK = 4 << 20


class CompressionError(RuntimeError):
    """Compressed input could not be read, or compressed output failed verification."""


def detect(path: Path) -> Algorithm | None:
    """The compression of `path` from its magic bytes (so misnamed files work too), or None for plain text."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head == _ZSTD_MAGIC:
        return "zstd"
    if head[:2] == _GZIP_MAGIC:
        return "gzip"
    return None


def check_single_zstd_frame(path: Path) -> None:
    """Refuse multi-frame .zst files (pzstd output, concatenated files): compress-utils 0.8 stops after the first
    frame without an error, which would silently drop mail. Walks frame and block headers only; no decoding."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if f.read(4) != _ZSTD_MAGIC:
            raise CompressionError(f"{path} is not a zstd frame")
        (fhd,) = f.read(1)
        fcs_flag, single_segment, checksum, dict_flag = fhd >> 6, (fhd >> 5) & 1, (fhd >> 2) & 1, fhd & 3
        skip = (0 if single_segment else 1) + (0, 1, 2, 4)[dict_flag]
        skip += (1 if single_segment else 0, 2, 4, 8)[fcs_flag]
        f.seek(skip, os.SEEK_CUR)
        while True:
            header = f.read(3)
            if len(header) < 3:
                raise CompressionError(f"{path} is truncated (incomplete zstd block header)")
            (value,) = struct.unpack("<I", header + b"\0")
            last, block_type, block_size = value & 1, (value >> 1) & 3, value >> 3
            if block_type == 3:
                raise CompressionError(f"{path} is corrupt (reserved zstd block type)")
            f.seek(1 if block_type == 1 else block_size, os.SEEK_CUR)
            if last:
                break
        end = f.tell() + (4 if checksum else 0)
    if end < size:
        raise CompressionError(
            f"{path} has more than one zstd frame (e.g. made by pzstd, or several files concatenated). "
            f"compress-utils would stop after the first frame, so decompress it with `zstd -d` first."
        )
    if end > size:
        raise CompressionError(f"{path} is truncated")


class DecompressingReader:
    """Decompressed lines from a compressed file. A thread feeds compress-utils (which releases the GIL) and hands
    large decoded chunks over a small queue, so decoding overlaps with parsing, as a separate `zstd -d` would."""

    _DONE = object()

    def __init__(self, path: Path, algorithm: Algorithm):
        self.path, self.algorithm = path, algorithm
        self.bytes_in = 0
        self.bytes_out = 0
        self._error: BaseException | None = None
        self._stop = threading.Event()
        if algorithm == "zstd":
            check_single_zstd_frame(path)
        self._queue: queue.Queue[object] = queue.Queue(maxsize=4)
        # compress-utils 0.8 re-takes the GIL many times per decompress() call, so with Python's default 5 ms
        # switch interval the decoder spends half its time waiting on the parsing thread. A shorter interval while
        # we read restores full overlap (measured: 8.8 s -> 4.9 s for 1.5 GB). Restored in close().
        self._switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(min(self._switch_interval, 0.0005))
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _put(self, item: object) -> bool:
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _pump(self) -> None:
        try:
            with open(self.path, "rb", buffering=0) as src:
                d = cu.DecompressStream(self.algorithm)
                while chunk := src.read(CHUNK):
                    data = d.decompress(chunk)
                    self.bytes_in += len(chunk)
                    self.bytes_out += len(data)
                    if data and not self._put(data):
                        return
                data = d.finish()
                self.bytes_out += len(data)
                if data and not self._put(data):
                    return
                if self.algorithm == "gzip":
                    self._check_gzip_size(src)
        except BaseException as e:  # noqa: BLE001 - re-raised in the reading thread by lines() / close()
            self._error = e
        finally:
            self._put(self._DONE)

    def _check_gzip_size(self, src: IO[bytes]) -> None:
        # The last 4 bytes of a gzip file are the size of the *last* member mod 2**32. compress-utils 0.8 stops after
        # the first member without an error, so a mismatch means dropped members (or corruption).
        src.seek(-4, os.SEEK_END)
        (isize,) = struct.unpack("<I", src.read(4))
        if isize != self.bytes_out % (1 << 32):
            raise CompressionError(
                f"{self.path} looks like a multi-member gzip file (e.g. made by pigz, or several files "
                f"concatenated), which compress-utils can't read yet; decompress it with `gzip -d` first"
            )

    def chunks(self) -> Iterator[bytes]:
        """Decompressed data in large chunks, in order. Raises if decoding failed."""
        while True:
            item = self._queue.get()
            if item is self._DONE:
                break
            assert isinstance(item, bytes)
            yield item
        self._raise_error()

    def _raise_error(self) -> None:
        if self._error is None:
            return
        if isinstance(self._error, CompressionError):
            raise self._error
        raise CompressionError(f"could not decompress {self.path}: {self._error}") from self._error

    def close(self) -> None:
        """Stop the feeder thread (safe to call at any point)."""
        self._stop.set()
        while True:  # unblock a feeder waiting on a full queue
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._thread.join()
        sys.setswitchinterval(self._switch_interval)


@dataclass(frozen=True)
class CompressResult:
    source: Path
    dest: Path
    algorithm: Algorithm
    level: int
    source_bytes: int
    compressed_bytes: int
    sha256: str
    """Of the uncompressed data, checked against a full decompression of `dest`."""
    source_deleted: bool

    @property
    def ratio(self) -> float:
        return self.source_bytes / self.compressed_bytes if self.compressed_bytes else 0.0


def compress_mbox(
    source: str | Path,
    dest: str | Path | None = None,
    *,
    algorithm: Algorithm = "zstd",
    level: int = 5,
    delete_source: bool = False,
    overwrite: bool = False,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> CompressResult:
    """Compress `source` to `dest` (default: `source` + `.zst` or `.gz`), then verify it by decompressing it in full
    and comparing size and SHA-256 with the original. Only after that succeeds, and only with `delete_source=True`,
    is the original deleted. If verification fails, `dest` is removed and the original is untouched.

    `level` is compress-utils' 1 (fastest) to 10 (smallest). `on_progress(phase, done, total)` reports bytes for the
    "compress" and "verify" phases.
    """
    source = Path(source)
    if not source.is_file():
        raise CompressionError(f"no such file: {source}")
    if detect(source) is not None:
        raise CompressionError(f"{source} is already compressed")
    if not 1 <= level <= 10:
        raise CompressionError(f"level must be 1 to 10, got {level}")
    dest = Path(dest) if dest is not None else source.with_name(source.name + EXTENSIONS[algorithm])
    if dest.exists() and not overwrite:
        raise CompressionError(f"{dest} already exists")
    total = source.stat().st_size
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        original = hashlib.sha256()
        c = cu.CompressStream(algorithm, level)
        done = 0
        with open(source, "rb", buffering=0) as src, open(tmp, "wb") as out:
            while chunk := src.read(CHUNK):
                original.update(chunk)
                out.write(c.compress(chunk))
                done += len(chunk)
                if on_progress:
                    on_progress("compress", done, total)
            out.write(c.finish())
        compressed_bytes = tmp.stat().st_size

        restored, restored_bytes = hashlib.sha256(), 0
        d = cu.DecompressStream(algorithm)
        read = 0
        with open(tmp, "rb", buffering=0) as f:
            while chunk := f.read(CHUNK):
                data = d.decompress(chunk)
                restored.update(data)
                restored_bytes += len(data)
                read += len(chunk)
                if on_progress:
                    on_progress("verify", read, compressed_bytes)
        data = d.finish()
        restored.update(data)
        restored_bytes += len(data)
        if restored_bytes != total or restored.digest() != original.digest():
            raise CompressionError(
                f"verification failed: decompressed {restored_bytes:,} bytes, expected {total:,} "
                f"(or checksums differ); {source} was left untouched"
            )
        os.replace(tmp, dest)
    except cu.CompressError as e:
        raise CompressionError(f"compressing {source} failed: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)

    if delete_source:
        source.unlink()
    return CompressResult(
        source, dest, algorithm, level, total, compressed_bytes, original.hexdigest(), source_deleted=delete_source
    )
