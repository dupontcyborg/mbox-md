"""Read an mbox file and yield raw messages."""

import hashlib
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

# Gmail Takeout separator: "From 1234567890@xxx Mon Jan 01 00:00:00 +0000 2024"
SEP = re.compile(rb"^From \d+@xxx \w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d [+-]\d{4} \d{4}\r?\n$")

# Decoding with a large window only raises zstd's memory limit, so accept archives written with up to --long=31.
ZSTD_WINDOW_LOG = 31


class ReadError(RuntimeError):
    """The input could not be read (missing file, missing or failing `zstd`)."""


class MboxStream:
    """Raw messages from a plain `.mbox` or a `.mbox.zst`, with progress in on-disk bytes.

    with MboxStream("All mail.mbox.zst") as stream:
        for raw in stream:
            print(stream.position / stream.total_bytes)
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ReadError(f"no such file: {self.path}")
        self.total_bytes = self.path.stat().st_size
        self._file: IO[bytes] | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._lines: IO[bytes] | None = None
        self._pump: threading.Thread | None = None
        self._stop = threading.Event()
        self._fed = 0
        self._exhausted = False

    def __enter__(self) -> "MboxStream":
        if self.path.suffix == ".zst":
            zstd = shutil.which("zstd")
            if zstd is None:
                raise ReadError(
                    "reading .zst input needs the zstd command (brew install zstd, apt install zstd, "
                    "or winget install zstd), or decompress the file first"
                )
            self._proc = subprocess.Popen(
                [zstd, "-d", "-q", f"--long={ZSTD_WINDOW_LOG}", "-c", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=8 << 20,
            )
            self._lines = self._proc.stdout
            # Feed zstd ourselves and count what we hand over. (Sharing the file offset with zstd and querying it
            # with lseek races on macOS and corrupts the stream.)
            self._pump = threading.Thread(target=self._feed_zstd, daemon=True)
            self._pump.start()
        else:
            self._file = self._lines = open(self.path, "rb", buffering=8 << 20)
        return self

    def _feed_zstd(self) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            with open(self.path, "rb", buffering=0) as f:
                while chunk := f.read(1 << 20):
                    if self._stop.is_set():
                        break
                    self._proc.stdin.write(chunk)
                    self._fed += len(chunk)
        except (BrokenPipeError, ValueError, OSError):
            pass  # zstd exited (we stopped reading early, or it failed; __exit__ reports that)
        finally:
            try:
                self._proc.stdin.close()
            except OSError:
                pass

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            if self._proc is not None:
                assert self._proc.stdout is not None and self._proc.stderr is not None
                self._stop.set()
                self._proc.stdout.close()
                stderr = self._proc.stderr.read().decode(errors="replace").strip()
                code = self._proc.wait()
                self._proc.stderr.close()
                if self._pump is not None:
                    self._pump.join()
                # Stopping early (--limit, an error) makes zstd fail with a broken pipe; only a full read counts.
                if code != 0 and self._exhausted and exc_type is None:
                    raise ReadError(f"zstd could not decompress {self.path}: {stderr or f'exit code {code}'}")
        finally:
            if self._file is not None:
                self._file.close()

    @property
    def position(self) -> int:
        """Bytes of the input file consumed so far."""
        if self._proc is not None:
            return self._fed
        if self._file is None or self._file.closed:
            return 0
        return self._file.tell()

    def __iter__(self) -> Iterator[bytes]:
        if self._lines is None:
            raise RuntimeError("use MboxStream as a context manager")
        yield from split_messages(iter(self._lines))
        self._exhausted = True


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
