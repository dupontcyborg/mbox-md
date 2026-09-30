# TODO

Current state (2026-09-29): the original single-file script `mbox_to_markdown.py` has been split into the `mbox_md` package under `src/` with no behavior change (output on the local sample is byte-identical to the script's). The script converted a 67,704-message Gmail Takeout mbox (15.4 GB) into 65,389 Markdown files in about 42 seconds. It works, but it has hard-coded choices and no tests, packaging, or docs. This list is what turns it into a reusable tool.

## Bugs found in the first real run

All fixed on 2026-09-30, with regression tests. Full archive after the fixes: 65,389 messages, 0 errors, 34 s on 16 cores.

- [x] Fixed attachment dedup across extensions: after the workers finish, `AttachmentIndex.consolidate` keeps one file per hash (a known extension first, then the most used one), rewrites the few Markdown links that pointed at the others, and deletes them. Full archive: 0 files missing from the index (was 72).
- [x] Fixed `ext_for`: it only trusts suffixes that `mimetypes` knows, or short suffixes that start with a letter and don't look like hex ids. Full archive: 0 junk extensions.
- [x] Fixed `message_key` to split headers on `\r?\n\r?\n`.
- [x] Fixed relative links in HTML bodies: relative, `#fragment`, and `javascript:` links keep only their text, and `//host` links become https. On the full archive, 158 relative links remain, all in 74 plain-text bodies (GitHub notifications quoting Markdown). That's the sender's text, so it's left as is.
- [x] Fixed forwarded `message/rfc822` attachments: they're saved as `<subject>.eml`. Full archive: 160 recovered.
- [x] Fixed malformed `From:` headers: when parsing finds no address, the raw unfolded value is kept.
- [x] Fixed the hang: the pool is now a bounded `ProcessPoolExecutor`, which raises `mbox_md.WorkerError` with a hint instead of hanging, and `workers=1` runs in-process with no pool.

## Licensing

- [x] Replaced the GPL-3.0 `html2text` with `markdownify` (MIT; its dependencies beautifulsoup4, soupsieve, and six are MIT too). Layout tables are unwrapped into paragraphs, and `<head>`, `<style>`, `<script>`, and images are dropped. On 2,000 real HTML-only bodies the output is about 10% smaller than html2text's, with far less table markup, but conversion is about 3x slower (4.7 ms vs 1.4 ms per body). lxml only saved 12%, so it isn't used.
- [x] Measured the markdownify slowdown end to end: none measurable (7.48 s with html2text vs 7.53 s with markdownify for the first 10,000 real messages on 16 cores). Only about 9% of messages are HTML-only, and conversion runs spread across the workers.
- [x] Spiked mdream (2026-09-30) on 2,000 real HTML-only bodies. Its Rust engine takes 0.08 ms per body and its JS engine 0.39 ms, versus 4.7 ms for markdownify, but it writes nested layout tables out as raw `<table><tr><td>` HTML in 47% of emails, and `tagOverrides` doesn't prevent it. Because conversion isn't the bottleneck, it isn't worth a TypeScript rewrite. If HTML speed ever matters, the Rust core is also published as the `mdream` crate (usable through PyO3), and `html-to-markdown` on PyPI (MIT, Rust, 0.48 ms per body) is a drop-in option.
- [x] The single-threaded reader bottleneck: replaced line-by-line splitting with chunked `bytes.find` splitting (see the Compression section). Further option: let workers read byte ranges of plain `.mbox` files themselves.

## Architecture and packaging

Goal: split the single script into small modules that can each be tested alone, then publish them as one wheel on PyPI that works both as a CLI and as a Python API. The CLI stays a thin layer over the API.

- [x] Use a `src/` layout with `pyproject.toml` (hatchling or uv_build), a `mbox-md` console script, and a `python -m mbox_md` entry point. Install with `uv tool install mbox-md` or `pipx install mbox-md`.
- [x] Name: `mbox-md` on PyPI (unclaimed as of 2026-09-29, as are `mboxmd`, `mbox2md`, `mbox-to-md`, `mbox-markdown`), import package `mbox_md`, command `mbox-md`. Reserve the name early with a first release.
- [x] Split into modules, each with one job and its own tests:
  - `_reader.py`: open plain, `.gz`, and `.zst` input and yield raw messages (separator detection, mboxrd unescaping).
  - `_parse.py`: raw bytes to a `ParsedMessage` dataclass (headers, labels, dates, body, attachment parts). Pure, no file I/O.
  - `_body.py`: choosing between plain and HTML, HTML-to-Markdown conversion, and cleanup.
  - `_naming.py`: slugs, filenames, and folder paths.
  - `_attachments.py`: an `AttachmentStore` (content-addressed, one file per hash) and extension detection.
  - `_render.py`: front matter and the Markdown document. Pure; takes a `ParsedMessage` and returns a string.
  - `_writer.py`: the output tree, `messages.jsonl`, and `attachments/index.jsonl`.
  - `_pipeline.py`: orchestration, Message-ID dedup, the worker pool, and stats.
  - `_options.py`: a frozen `ConvertOptions` dataclass that holds every setting, shared by the API and the CLI.
  - `_cli.py`: argument parsing and progress display only.
- [x] Public API: `mbox_md.convert(src, outdir, options=ConvertOptions(...)) -> ConvertStats`, plus `iter_messages()`, `parse_message()`, and `WorkerError`. All other modules are private (`_reader.py`, `_parse.py`, ...), the package has type hints and a `py.typed` marker, and it passes `mypy --strict`.
- [x] Deleted `mbox_to_markdown.py`; the package replaces it.
- [x] Keep attachment writes inside the workers, behind the store interface, so attachment bytes aren't sent back to the parent process. Keep the pure steps (parse and render) free of I/O so they can be tested directly.
- [x] Polished the CLI: a `rich` progress bar (bytes, messages, messages per second, ETA), a summary table, clear errors with exit codes (1 for unreadable input, 130 for Ctrl-C, with prompt shutdown), and `--quiet`, `--verbose`, and `--json`. Kept argparse rather than typer; subcommands can wait until there's a second command.
- [x] Runtime dependencies: markdownify, rich, and compress-utils, all MIT.
- [x] Branch coverage with coverage.py, including worker processes and the zstd feeder thread. CI enforces a 95% minimum on the Ubuntu / Python 3.13 job and posts the report to the job summary; measured 98% on 2026-09-30. Raise `fail_under` in `pyproject.toml` as coverage grows.
- [ ] Tooling: pre-commit.
- [x] CI with GitHub Actions (`.github/workflows/ci.yml`): ruff, mypy, and a fixture freshness check; pytest on Python 3.11 to 3.14 on Linux, macOS, and Windows; wheel and sdist builds with a clean-environment smoke test. `release.yml` publishes to PyPI via trusted publishing when a `v*` tag matching `__version__` is pushed.
- [x] Explicitly list what goes into the sdist and wheel, so `local-data/` or any stray real mail can never be packaged (done in `pyproject.toml`).
- [x] Check the built artifacts in CI: `.github/scripts/check_dist.py` fails on any wheel or sdist file outside an allow-list.
- [ ] Set up the trusted publisher on PyPI (project `mbox-md`, workflow `release.yml`, environment `release`) and create the `release` environment in the GitHub repo settings before the first release.
- [ ] Versioning: SemVer starting at `0.1.0`, a `CHANGELOG.md`, and a single version source.
- [x] Added an MIT `LICENSE` and `license = "MIT"` metadata.

## CLI flags for currently hard-coded choices

- [x] `--skip-labels` (default `Spam,Trash`; `""` keeps everything).
- [x] `--min-inline-image` (default 5KB; accepts `0`, `5k`, `1.5MB`).
- [x] `--workers`, `--limit`, `--since`, and `--until` (inclusive days in the sender's timezone, like the output folders; undated mail is skipped when a range is set).
- [x] `--dry-run`: full parse without writing; on the real archive its estimates matched a real run exactly.
- [x] `--strip-quotes` (opt-in heuristic: `>` lines, the "On ... wrote:" line above them including two-line wraps, and Outlook "Original Message" tails).
- [x] Progress bar (see above). For `.zst` input, Python feeds zstd from a thread and counts bytes. An earlier version queried a file offset shared with zstd via `lseek`, which raced on macOS and corrupted the stream; a test now checks the stream stays identical while the position is read.

## Format support and robustness

- [ ] Support mbox flavors other than Gmail Takeout. The separator regex currently only accepts `From <digits>@xxx <date>` lines, so add a generic mboxrd/mboxo fallback and unescape `>From ` lines.
- [x] Read gzip- and zstd-compressed inputs (compress-utils).
- [ ] Handle unknown or invalid charsets (the `unknown-8bit` fallback exists; add tests for it and for other bad encodings).
- [ ] Decide how to treat messages without a readable body, and whether to note that in the front matter. Counts on the full archive: 275 have no body part at all (`body_none` in the stats), and 579 render as `_(no body)_` (522 attachment-only, 57 truly empty). Both numbers are right; they measure different things.
- [ ] Keep failed messages in `_unparsed/` as raw `.eml` files and write a short report of what failed and why.
- [ ] Make re-runs cheap: skip messages whose output file already exists, or write only changed files.
- [ ] Make re-runs clean: today they overwrite files but never remove stale `.md` files or orphaned attachments left by an earlier run with different naming. Add a cleanup mode or a manifest of files each run owns.
- [ ] Consider a checkpoint or resume option for multi-hour runs on very large archives.
- [ ] Confirm Message-ID dedup behavior and document it (first occurrence wins; labels are not merged today). Consider merging labels from duplicates.
- [ ] Folder and filename dates use each message's own `Date` offset, so mail lands on the sender's calendar day, not the reader's. Normalize to UTC or a `--tz` option, or document the behavior.
- [x] `.zst` input now decodes with `--long=31`, so archives written with any window up to 2 GB work. zstd's stderr is captured, so an early stop no longer prints "Broken pipe", and real decode errors raise `mbox_md.ReadError`.

## Tests

- [x] Build small synthetic mbox fixtures. Use fabricated data only, never real email.
- [x] Committed `.mbox` fixtures in `tests/fixtures/`, generated by `tests/fixtures/generate.py` (8 files, 76 KB, all fabricated, reserved domains only). `tests/test_fixtures.py` fails if a committed fixture differs from the generator's output or uses a non-reserved domain. Known gaps are `xfail(strict=True)` tests: attachment dedup across extensions, forwarded `message/rfc822`, malformed From, label merging for duplicates, hidden preheaders, relative links, mboxrd unescaping, and CRLF keys.
- [ ] Cover filename collisions (two messages in the same minute with the same slug and the same 4-hex id prefix).
- [x] Real reference dataset: `local-data/takeout-sample.mbox` (145 real messages, 6.5 MB, copied from a small Takeout export on 2026-09-29) is gitignored and must never be committed or packaged. Use it only for opt-in smoke tests (for example `pytest -m local`) that skip when the file is absent, and assert on counts and structure, never on message content.
- [x] Cover multipart mail, HTML-only mail, plain-text-only mail, and messages with no subject or no date.
- [x] Cover bad or unknown charsets and malformed headers, and CRLF line endings.
- [x] Cover duplicate Message-IDs.
- [x] Cover the attachment store: identical bytes under different names and different extensions dedupe to one file (this currently fails, see Bugs), and same name with different content stays separate.
- [x] Cover Spam and Trash skipping, and small inline image dropping.
- [x] Cover the filename scheme (`YYYY-MM-DD-HHMM-<slug>-<id4>.md`) for unicode, non-Latin, and very long subjects.
- [x] Check that the relative links in the Markdown resolve to real files at every folder depth.

## Compression (compress-utils)

- [x] Read `.mbox.zst` and `.mbox.gz` in-process with compress-utils (detected from magic bytes); the `zstd` CLI is no longer used or needed. Decoding runs in a thread that overlaps with parsing. Verified on the full archive: all 67,704 messages byte-identical to `zstd -dc`.
- [x] `--compress-source zstd|gzip`, `--compress-level`, and `--delete-source` (plus `mbox_md.compress_mbox`): compress next to the input, verify by a full decompression (size and SHA-256), and delete the original only after that and only when asked. On failure the output is removed and the original is untouched.
- [x] Reader speedup: messages are now split from large chunks with `bytes.find` plus a regex check instead of Python line iteration (209M lines on the full archive). Full conversion: 38.5 s, vs 34.3 s with the old zstd-CLI reader and 42 s for the original script. The remaining gap is compress-utils decode speed; see below.
- [ ] Support concatenated input once compress-utils does (then drop the guards in `_compression.py`).
- [ ] Switch `--compress-source` to long-distance matching once compress-utils has it. On a 500 MB slice of the real archive, zstd `--long=27` gets 3.70x vs 3.00x without it.

### compress-utils issues found while integrating (0.8.0)

- [ ] Streaming decode silently stops after the first gzip member or zstd frame: `cat a.gz b.gz` or pzstd output loses data without an error. mbox-md refuses these files for now (a zstd frame walk before decoding, and a gzip ISIZE check after).
- [ ] `DecompressStream.decompress()` re-acquires the GIL many times per call: decoding 1.5 GB takes 4.4 s alone and 8.8 s next to a busy Python thread (4.9 s with a 0.5 ms switch interval, which mbox-md sets while reading as a workaround). Releasing the GIL for the whole call would fix it.
- [ ] Large `decompress()` inputs are slow: 32 MB chunks take 18.1 s for the same 1.5 GB that takes 4.4 s in 4 MB chunks, which suggests the output buffer is regrown and copied repeatedly.
- [ ] Decode throughput: 762 MB/s vs 1,280 MB/s for the zstd CLI on the full archive.
- [ ] The PyPI wheel ships `.pyi` stubs but no `py.typed` marker, despite the README, so mypy treats the package as untyped (mbox-md sets `follow_untyped_imports` as a workaround).
- [ ] No window/long-distance option and no multithreaded compression (planned).
- [ ] zstd levels 6-10 fall off a cliff: 133 MB/s at level 5, 24 MB/s at 6, 6 MB/s at 10, for about 3% better ratio.
- [ ] No macOS x86_64 wheel, so Intel Macs build from the sdist (needs a compiler); now that mbox-md depends on it, that affects installs there.

## Docs

- [ ] Write a README with the problem, install, quick start, and a diagram of the output layout.
- [ ] Document the design rationale: content-addressed attachments, hash-based naming, and why Spam and Trash are skipped by default.
- [ ] Document the front matter fields and the `messages.jsonl` and `attachments/index.jsonl` formats.
- [ ] Enrich `messages.jsonl` with date, from, labels, and thread_id so it works as an index without parsing front matter.
- [ ] Add a "how to get your Gmail export" section covering Google Takeout.
- [ ] Add a comparison with existing tools (mboxer, mbox-to-md, mbox2md, Mbox-Converter, gmail_to_md, mail2markdown).
- [ ] Include benchmark numbers (15.4 GB in about 42 seconds on 16 cores, 36% attachment dedup) and describe the hardware.

## Later / maybe

- [ ] SQLite or FTS index of the output for fast search.
- [ ] Thread grouping, with a `threads/` view or thread-level Markdown files.
- [ ] Optional export of `.ics` attachments inline as event summaries.
- [ ] An Obsidian-friendly mode (wikilinks, tags from labels).
- [ ] Other sources beyond mbox, such as Maildir and `.eml` folders.
