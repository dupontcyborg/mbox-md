"""Convert mbox archives (Gmail Takeout first) into Markdown files with deduplicated attachments.

    import mbox_md
    stats = mbox_md.convert("All mail.mbox", "out/")

Or, without writing anything:

    for raw in mbox_md.iter_messages("All mail.mbox"):
        msg = mbox_md.parse_message(raw)
"""

from ._compression import CompressionError, CompressResult, compress_mbox
from ._options import ConvertOptions
from ._parse import AttachmentPart, ParsedMessage, parse_message
from ._pipeline import ConvertStats, WorkerError, convert
from ._reader import ReadError, iter_messages

__version__ = "0.1.0.dev0"

__all__ = [
    "AttachmentPart",
    "CompressResult",
    "CompressionError",
    "ConvertOptions",
    "ConvertStats",
    "ParsedMessage",
    "ReadError",
    "WorkerError",
    "__version__",
    "compress_mbox",
    "convert",
    "iter_messages",
    "parse_message",
]
