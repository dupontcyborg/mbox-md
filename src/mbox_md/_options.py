"""Every conversion setting, shared by the Python API and the CLI."""

import os
from dataclasses import dataclass, field
from datetime import date


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


@dataclass(frozen=True)
class ConvertOptions:
    skip_labels: frozenset[str] = field(default_factory=lambda: frozenset({"Spam", "Trash"}))
    """Messages carrying any of these Gmail labels are skipped."""
    min_inline_image: int = 5 * 1024
    """Inline images smaller than this many bytes (signature logos, tracking pixels) are dropped."""
    max_html: int = 2_000_000
    """HTML bodies are truncated to this many characters before conversion."""
    since: date | None = None
    """Only convert messages dated on or after this day (in the sender's timezone, like the output folders)."""
    until: date | None = None
    """Only convert messages dated on or before this day. With `since` or `until` set, undated mail is skipped."""
    strip_quotes: bool = False
    """Drop quoted replies (`> ...` lines, their "On ... wrote:" line, and forwarded "Original Message" tails)."""
    dry_run: bool = False
    """Parse and count everything, but write nothing."""
    workers: int = field(default_factory=default_workers)
    limit: int | None = None
    """Stop after reading this many messages from the mbox."""

    def in_range(self, day: date | None) -> bool:
        if self.since is None and self.until is None:
            return True
        if day is None:
            return False
        return (self.since is None or day >= self.since) and (self.until is None or day <= self.until)
