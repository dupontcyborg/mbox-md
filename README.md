# mbox-md

Convert a Gmail Takeout (or other) `.mbox` archive into one Markdown file per message, organized by year and month, with attachments stored once and shared across messages.

Work in progress.

## Usage

CLI:

```sh
mbox-md "All mail Including Spam and Trash.mbox" archive/
mbox-md takeout.mbox.zst archive/ --since 2020-01-01 --strip-quotes
mbox-md takeout.mbox --dry-run            # counts and output size, writes nothing
mbox-md takeout.mbox archive/ --compress-source zstd --delete-source
```

| Option | What it does |
|---|---|
| `--since`, `--until YYYY-MM-DD` | only messages in this date range (undated mail is skipped when a range is set) |
| `--skip-labels LABELS` | comma-separated Gmail labels to skip; default `Spam,Trash`, `""` keeps everything |
| `--limit N` | stop after reading N messages |
| `--tz ZONE` | timezone for folder and file names and `--since`/`--until`: `sender` (default), `utc`, `local`, or an IANA name |
| `--min-inline-image SIZE` | drop inline images smaller than this (logos, tracking pixels); default `5KB`, `0` keeps all |
| `--strip-quotes` | drop quoted replies from bodies |
| `--keep-from-escapes` | don't undo mboxrd `>From ` escaping (for mboxo files) |
| `--workers N` | worker processes; default CPUs − 1 |
| `--dry-run` | parse and count everything, write nothing |
| `--incremental` | convert only messages not already in the output (a newer export, or resuming a stopped run) |
| `--prune` | delete files that earlier runs wrote into the output and this run didn't |
| `--compress-source zstd\|gzip` | after converting, compress the input mbox next to it and verify it by decompressing it in full |
| `--compress-level 1-10` | compression level, 1 fastest to 10 smallest (default 5) |
| `--delete-source` | delete the original mbox, only after the compressed copy verifies |
| `-q`, `-v`, `--json` | quiet, report each failure, or print stats as JSON |

Input can be plain, zstd, or gzip; compression is detected from the file's contents and handled in-process by [compress-utils](https://pypi.org/project/compress-utils/), with no `zstd` or `gzip` command needed.

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
uv run coverage run -m pytest && uv run coverage combine && uv run coverage report   # branch coverage, 95% minimum
uv run ruff check src tests && uv run ruff format --check src tests
```

## License

[MIT](./LICENSE)
