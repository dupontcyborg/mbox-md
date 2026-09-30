"""Read an mbox file and yield raw messages."""

import contextlib
import hashlib
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

# Gmail Takeout separator: "From 1234567890@xxx Mon Jan 01 00:00:00 +0000 2024"
SEP = re.compile(rb"^From \d+@xxx \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d [+-]\d{4} \d{4}\r?\n$")


@contextlib.contextmanager
def open_mbox(path: str | Path) -> Iterator[BinaryIO]:
    """Plain .mbox, or a .zst streamed through `zstd -d` (no decompressed copy on disk)."""
    if str(path).endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-d", "--long=27", "-c", str(path)], stdout=subprocess.PIPE, bufsize=8 << 20)
        assert proc.stdout is not None
        try:
            yield proc.stdout
        finally:
            proc.stdout.close()
            proc.wait()
    else:
        with open(path, "rb", buffering=8 << 20) as f:
            yield f


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
    with open_mbox(path) as f:
        yield from split_messages(iter(f))


def message_key(raw: bytes) -> bytes:
    """The Message-ID, or a hash of the headers when there is none. Used for dedup."""
    head = raw.split(b"\n\n", 1)[0]
    m = re.search(rb"(?im)^message-id:\s*(<[^>]+>)", head)
    return m.group(1) if m else hashlib.sha1(head).digest()


def message_hash(raw: bytes) -> str:
    """Stable hex id for a message, used in filenames and `_unparsed/` names."""
    return hashlib.sha1(message_key(raw)).hexdigest()
