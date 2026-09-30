"""Convert mbox archives (Gmail Takeout first) into Markdown files with deduplicated attachments.

    import mbox_md
    stats = mbox_md.convert("All mail.mbox", "out/")

Or, without writing anything:

    for raw in mbox_md.iter_messages("All mail.mbox"):
        msg = mbox_md.parse_message(raw)
"""

from .options import ConvertOptions
from .parse import AttachmentPart, ParsedMessage, parse_message
from .pipeline import ConvertStats, WorkerError, convert
from .reader import iter_messages

__version__ = "0.1.0.dev0"

__all__ = [
    "AttachmentPart",
    "ConvertOptions",
    "ConvertStats",
    "ParsedMessage",
    "__version__",
    "convert",
    "iter_messages",
    "WorkerError",
    "parse_message",
]
