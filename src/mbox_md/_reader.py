"""Read an mbox file and yield raw messages."""

import functools
import hashlib
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

from ._compression import CHUNK, CompressionError, DecompressingReader, detect

# Gmail Takeout separator: "From 1234567890@xxx Mon Jan 01 00:00:00 +0000 2024"
SEP = re.compile(rb"^From \d+@xxx \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d [+-]\d{4} \d{4}\r?\n$")


class ReadError(RuntimeError):
    """The input could not be read (missing file, corrupt or unsupported compression)."""


class MboxStream:
    """Raw messages from a plain, zstd, or gzip mbox, with progress in on-disk bytes. Compression is detected from
    the file's magic bytes and decoded in-process by compress-utils.

    with MboxStream("All mail.mbox.zst") as stream:
        for raw in stream:
            print(stream.position / stream.total_bytes)
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ReadError(f"no such file: {self.path}")
        self.total_bytes = self.path.stat().st_size
        self.compression = detect(self.path) if self.total_bytes else None
        if self.compression is None and self.path.suffix.lower() in (".zst", ".gz"):
            raise ReadError(f"{self.path} is named like a compressed file but isn't zstd or gzip")
        self._file: IO[bytes] | None = None
        self._decoder: DecompressingReader | None = None
        self._chunks: Iterator[bytes] | None = None
        self._exhausted = False

    def __enter__(self) -> "MboxStream":
        if self.compression is not None:
            try:
                self._decoder = DecompressingReader(self.path, self.compression)
            except CompressionError as e:
                raise ReadError(str(e)) from e
            self._chunks = self._decoder.chunks()
        else:
            self._file = open(self.path, "rb", buffering=0)
            self._chunks = iter(functools.partial(self._file.read, CHUNK), b"")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._decoder is not None:
            self._decoder.close()
        if self._file is not None:
            self._file.close()

    @property
    def position(self) -> int:
        """Bytes of the input file consumed so far."""
        if self._decoder is not None:
            return self._decoder.bytes_in
        if self._file is None or self._file.closed:
            return 0
        return self._file.tell()

    def __iter__(self) -> Iterator[bytes]:
        if self._chunks is None:
            raise RuntimeError("use MboxStream as a context manager")
        try:
            yield from split_chunks(self._chunks)
        except CompressionError as e:
            raise ReadError(str(e)) from e
        self._exhausted = True


# SEP without anchors or the end-of-string `$`, for matching at a known offset inside a buffer.
_SEP_AT = re.compile(rb"From \d+@xxx \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d [+-]\d{4} \d{4}\r?\n")
_MAX_SEP = 128  # longer than any separator line plus the blank line before it


def _after_blank_line(buf: bytes, pos: int, at_stream_start: bool) -> bool:
    """True if the line before `pos` is empty (`\\n` or `\\r\\n`), or `pos` is the very start of the stream."""
    if pos == 0:
        return at_stream_start
    if buf[pos - 1 : pos] != b"\n":
        return False
    before = buf[pos - 2 : pos - 1]
    if pos == 1 or before == b"\n":
        return True
    return before == b"\r" and (pos == 2 or buf[pos - 3 : pos - 2] == b"\n")


def split_chunks(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Split an mbox arriving in arbitrary chunks into raw messages, without iterating line by line.

    Same rules as `split_messages`: a separator line only counts after a blank line (or at the start), and is not
    part of either message.
    """
    buf = b""
    start: int | None = None  # where the current message begins in buf, once its separator has been seen
    scan = 0  # where to resume searching for separators
    offset = 0  # stream offset of buf[0], to know whether a match is at the very start of the stream
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        while True:
            # bytes.find is memchr-fast; only candidates at a line start get the full regex.
            pos = buf.find(b"From ", scan)
            if pos == -1:
                scan = max(scan, len(buf) - len(b"From "))  # a "From" cut by the chunk end is re-checked
                break
            if pos > 0 and buf[pos - 1] != 0x0A:
                scan = pos + 1
                continue
            m = _SEP_AT.match(buf, pos)
            if m is None:
                if len(buf) - pos < _MAX_SEP:
                    scan = pos
                    break  # possibly a separator cut off by the chunk end; retry here with more data
                scan = pos + 1
                continue
            if not _after_blank_line(buf, pos, at_stream_start=offset == 0):
                scan = pos + 1
                continue
            if start is not None:
                yield buf[start:pos]
            elif pos > 0:
                yield buf[:pos]  # content before the first separator counts as a message, like split_messages
            start = scan = m.end()
        # Drop what's been emitted. The current message starts at a line boundary, so the blank-line lookbehind
        # never needs bytes before it. (Before the first separator nothing is dropped: that content is a message.)
        if start:
            buf, offset, scan, start = buf[start:], offset + start, scan - start, 0
    if start is not None:
        if buf[start:]:
            yield buf[start:]
    elif buf:
        yield buf


def split_messages(lines: Iterator[bytes]) -> Iterator[bytes]:
    """Split mbox lines into raw messages. A separator only counts after a blank line."""
    buf: list[bytes] = []
    prev_blank = True
    for line in lines:
        if line.startswith(b"From ") and prev_blank and SEP.match(line):
            if buf:
                yield b"".join(buf)
            buf = []
        else:
            buf.append(line)
        prev_blank = line in (b"\n", b"\r\n")
    if buf:
        yield b"".join(buf)


def iter_messages(path: str | Path) -> Iterator[bytes]:
    """Yield each message in the mbox as raw RFC 822 bytes."""
    with MboxStream(path) as stream:
        yield from stream


def message_key(raw: bytes) -> bytes:
    """The Message-ID, or a hash of the headers when there is none. Used for dedup."""
    head = re.split(rb"\r?\n\r?\n", raw, maxsplit=1)[0]
    m = re.search(rb"(?im)^message-id:\s*(<[^>]+>)", head)
    return m.group(1) if m else hashlib.sha1(head).digest()


def message_hash(raw: bytes) -> str:
    """Stable hex id for a message, used in filenames and `_unparsed/` names."""
    return hashlib.sha1(message_key(raw)).hexdigest()
