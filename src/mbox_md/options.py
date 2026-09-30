"""Every conversion setting, shared by the Python API and the CLI."""

import os
from dataclasses import dataclass, field


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
    workers: int = field(default_factory=default_workers)
    limit: int | None = None
    """Stop after reading this many messages from the mbox."""
