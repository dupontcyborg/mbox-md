# mbox-md

Convert a Gmail Takeout (or other) `.mbox` archive into one Markdown file per message, organized by year and month, with attachments stored once and shared across messages.

Work in progress.

## Usage

CLI:

```sh
mbox-md "All mail Including Spam and Trash.mbox" archive/
mbox-md takeout.mbox.zst archive/ --since 2020-01-01 --strip-quotes
mbox-md takeout.mbox --dry-run            # counts and output size, writes nothing
```

| Option | What it does |
|---|---|
| `--since`, `--until YYYY-MM-DD` | only messages in this date range (undated mail is skipped when a range is set) |
| `--skip-labels LABELS` | comma-separated Gmail labels to skip; default `Spam,Trash`, `""` keeps everything |
| `--limit N` | stop after reading N messages |
| `--min-inline-image SIZE` | drop inline images smaller than this (logos, tracking pixels); default `5KB`, `0` keeps all |
| `--strip-quotes` | drop quoted replies from bodies |
| `--workers N` | worker processes; default CPUs − 1 |
| `--dry-run` | parse and count everything, write nothing |
| `-q`, `-v`, `--json` | quiet, report each failure, or print stats as JSON |

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
uv run mypy              # strict type checking
uv run ruff check src tests && uv run ruff format --check src tests
```

## License

[MIT](./LICENSE)
