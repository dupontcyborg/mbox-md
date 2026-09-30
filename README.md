# mbox-md

Convert a Gmail Takeout (or other) `.mbox` archive into one Markdown file per message, organized by year and month, with attachments stored once and shared across messages.

Work in progress.

## Usage

CLI:

```sh
uv run mbox-md "All mail Including Spam and Trash.mbox" out/
```

API:

```python
import mbox_md

stats = mbox_md.convert("All mail.mbox", "out/")

for raw in mbox_md.iter_messages("All mail.mbox"):
    msg = mbox_md.parse_message(raw)  # None if it's in Spam or Trash
```

## Development

```sh
uv sync
uv run pytest            # unit tests, fabricated data only
uv run pytest -m local   # smoke test against local-data/takeout-sample.mbox, if you have it
```
