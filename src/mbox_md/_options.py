"""Every conversion setting, shared by the Python API and the CLI."""

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


@cache
def resolve_tz(name: str) -> tzinfo | None:
    """`sender` -> None (keep each message's own offset), `utc`, `local`, or an IANA name like `Europe/Paris`."""
    if name == "sender":
        return None
    if name == "utc":
        return UTC
    if name == "local":
        local = datetime.now().astimezone().tzinfo
        assert local is not None
        return local
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"unknown timezone {name!r} (use sender, utc, local, or an IANA name like Europe/Paris)"
        ) from None


@dataclass(frozen=True)
class ConvertOptions:
    skip_labels: frozenset[str] = field(default_factory=lambda: frozenset({"Spam", "Trash"}))
    """Messages carrying any of these Gmail labels are skipped."""
    min_inline_image: int = 5 * 1024
    """Inline images smaller than this many bytes (signature logos, tracking pixels) are dropped."""
    max_html: int = 2_000_000
    """HTML bodies are truncated to this many characters before conversion."""
    since: date | None = None
    """Only convert messages dated on or after this day (in `tz`, like the output folders)."""
    until: date | None = None
    """Only convert messages dated on or before this day. With `since` or `until` set, undated mail is skipped."""
    tz: str = "sender"
    """Timezone for folder and file names and for `since`/`until`: `sender` (each message's own offset), `utc`,
    `local`, or an IANA name. Front matter always keeps the original timestamp and offset."""
    strip_quotes: bool = False
    """Drop quoted replies (`> ...` lines, their "On ... wrote:" line, and forwarded "Original Message" tails)."""
    unescape_from: bool = True
    """Undo mboxrd `>From ` escaping (Gmail Takeout escapes this way). Turn off for mboxo files where `>From ` in a
    body is literal text."""
    incremental: bool = False
    """Skip messages already listed in the output's `messages.jsonl`: convert only new mail, or resume a run that
    was interrupted. Labels of already-converted messages are updated if they changed."""
    prune: bool = False
    """After a full run, delete files that earlier runs wrote and this run didn't (only files recorded in the
    previous `messages.jsonl`, `attachments/index.jsonl`, and `_unparsed/`)."""
    dry_run: bool = False
    """Parse and count everything, but write nothing."""
    workers: int = field(default_factory=default_workers)
    limit: int | None = None
    """Stop after reading this many messages from the mbox."""

    def __post_init__(self) -> None:
        resolve_tz(self.tz)  # fail fast on a bad name
        if self.incremental and self.prune:
            raise ValueError("incremental and prune can't be combined: an incremental run doesn't see every message")

    def localize(self, when: datetime | None) -> datetime | None:
        """`when` in the configured timezone, for naming and date filters."""
        tz = resolve_tz(self.tz)
        return when if when is None or tz is None else when.astimezone(tz)

    def in_range(self, when: datetime | None) -> bool:
        if self.since is None and self.until is None:
            return True
        local = self.localize(when)
        if local is None:
            return False
        day = local.date()
        return (self.since is None or day >= self.since) and (self.until is None or day <= self.until)
