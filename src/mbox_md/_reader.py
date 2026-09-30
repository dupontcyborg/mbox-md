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
# An mbox "From_" separator line: "From <sender> <asctime date>", optionally with a timezone before the year and
# anything after it ("remote from ..."). Covers Gmail Takeout ("From 1234@xxx Mon Jan 15 09:30:00 +0000 2024"),
# mboxo/mboxrd files from mail clients ("From alice@example.com Mon Jan  5 09:30:00 2024"), and MAILER-DAEMON lines.
_SEP_BODY = (
    rb"From \S* +[A-Z][a-z]{2} +[A-Z][a-z]{2} +\d{1,2} +\d{1,2}:\d{2}(?::\d{2})?"
    rb"(?: +(?:[+-]\d{4}|[A-Z]{1,5}))? +\d{4}(?: [^\r\n]*)?\r?\n"
)
SEP = re.compile(b"^" + _SEP_BODY + b"$")
_ESCAPED_FROM = re.compile(rb"^>(>*From )", re.MULTILINE)


def unescape_from_lines(raw: bytes) -> bytes:
    """Undo mboxrd escaping: `>From ` -> `From `, `>>From ` -> `>From `. Gmail Takeout escapes this way."""
    return _ESCAPED_FROM.sub(rb"\1", raw) if b">From " in raw else raw


class ReadError(RuntimeError):
    """The input could not be read (missing file, corrupt or unsupported compression)."""


class MboxStream:
    """Raw messages from a plain, zstd, or gzip mbox, with progress in on-disk bytes. Compression is detected from
    the file's magic bytes and decoded in-process by compress-utils.

    with MboxStream("All mail.mbox.zst") as stream:
        for raw in stream:
            print(stream.position / stream.total_bytes)
    """

    def __init__(self, path: str | Path, *, unescape_from: bool = True):
        self.path = Path(path)
        self.unescape_from = unescape_from
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
            yield from split_chunks(self._chunks, unescape_from=self.unescape_from)
        except CompressionError as e:
            raise ReadError(str(e)) from e
        self._exhausted = True


# SEP without anchors, for matching at a known offset inside a buffer.
_SEP_AT = re.compile(_SEP_BODY)
_MAX_SEP = 1024  # longer than any separator line plus the blank line before it


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


def split_chunks(chunks: Iterable[bytes], *, unescape_from: bool = False) -> Iterator[bytes]:
    """Split an mbox arriving in arbitrary chunks into raw messages, without iterating line by line.

    Same rules as `split_messages`: a separator line only counts after a blank line (or at the start), and is not
    part of either message.
    """
    buf = b""
    start: int | None = None  # where the current message begins in buf, once its separator has been seen
    scan = 0  # where to resume searching for separators
    offset = 0  # stream offset of buf[0], to know whether a match is at the very start of the stream
    escaped = False  # the current message has a ">From " line (found by the same scan, so no extra pass)

    def emit(message: bytes) -> bytes:
        return unescape_from_lines(message) if escaped else message

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
                if unescape_from and buf[pos - 1] == 0x3E:  # ">From ": mboxrd-escaped if only '>' precede it
                    line_start = buf.rfind(b"\n", 0, pos) + 1
                    escaped = escaped or buf[line_start:pos].strip(b">") == b""
                scan = pos + 1
                continue
            m = _SEP_AT.match(buf, pos)
            if m is None:
                if buf.find(b"\n", pos, pos + _MAX_SEP) == -1 and len(buf) - pos < _MAX_SEP:
                    scan = pos
                    break  # the line is cut off by the chunk end and might still be a separator; retry with more data
                scan = pos + 1  # a complete line that isn't a separator
                continue
            if not _after_blank_line(buf, pos, at_stream_start=offset == 0):
                scan = pos + 1
                continue
            if start is not None:
                yield emit(buf[start:pos])
            elif pos > 0:
                yield emit(buf[:pos])  # content before the first separator counts as a message, like split_messages
            start = scan = m.end()
            escaped = False
        # Drop what's been emitted. The current message starts at a line boundary, so the blank-line lookbehind
        # never needs bytes before it. (Before the first separator nothing is dropped: that content is a message.)
        if start:
            buf, offset, scan, start = buf[start:], offset + start, scan - start, 0
    if start is not None:
        if buf[start:]:
            yield emit(buf[start:])
    elif buf:
        yield emit(buf)


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


def iter_messages(path: str | Path, *, unescape_from: bool = True) -> Iterator[bytes]:
    """Yield each message in the mbox as raw RFC 822 bytes, with mboxrd `>From ` escaping undone by default."""
    with MboxStream(path, unescape_from=unescape_from) as stream:
        yield from stream


_BLANK_LINE = re.compile(rb"\n\r?\n")  # starts with a literal, so the regex engine can scan for it quickly


def header_block(raw: bytes) -> bytes:
    """The headers of a raw message: everything before the first blank line. Slices instead of splitting, so the
    body is never copied (this runs in the main process for every message)."""
    m = _BLANK_LINE.search(raw)
    return raw[: m.start()].rstrip(b"\r") if m else raw


def message_key(raw: bytes) -> bytes:
    """The Message-ID, or a hash of the headers when there is none. Used for dedup."""
    return key_from_head(header_block(raw))


def key_from_head(head: bytes) -> bytes:
    m = re.search(rb"(?im)^message-id:\s*(<[^>]+>)", head)
    return m.group(1) if m else hashlib.sha1(head).digest()


def message_hash(raw: bytes) -> str:
    """Stable hex id for a message, used in filenames and `_unparsed/` names."""
    return hashlib.sha1(message_key(raw)).hexdigest()
