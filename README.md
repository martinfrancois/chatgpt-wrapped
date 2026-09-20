# chatgpt-wrapped

Generate local HTML reports from the `Conversations` folder in a ChatGPT data export.

The repository provides two commands:

- `chatgpt-wrapped-report`: a detailed conversation history field report with charts, tables, yearly comparisons, and a JSON stats file.
- `chatgpt-wrapped-all-time`: an all-time “wrapped” style HTML summary with a paired JSON stats file.

Generated reports can contain private conversation titles, message-derived words, model usage, links, and activity patterns. The default `.gitignore` excludes outputs and raw export files.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Usage

Run the detailed report:

```bash
chatgpt-wrapped-report /path/to/ChatGPT-export/User\ Online\ Activity/Conversations \
  --output-dir output/conversation-report \
  --timezone Europe/Zurich
```

Run the wrapped report:

```bash
chatgpt-wrapped-all-time /path/to/ChatGPT-export/User\ Online\ Activity/Conversations \
  --output-dir output/chatgpt-wrapped-all-time \
  --timezone Europe/Zurich
```

Each command writes one HTML file and one stats JSON file to its output directory.

## Try a synthetic example

The included fixture contains one invented conversation with four messages.
Run both reports without supplying any personal data:

```bash
chatgpt-wrapped-report examples/synthetic-export \
  --output-dir output/example-report --timezone UTC
chatgpt-wrapped-all-time examples/synthetic-export \
  --output-dir output/example-wrapped --timezone UTC
```

Open `output/example-report/chatgpt-conversation-report.html` or
`output/example-wrapped/chatgpt-wrapped-all-time.html` in your browser. Each
directory also contains the matching stats JSON file. The small fixture shows
the report layout; it does not represent a typical conversation history.

## Input and privacy

The generators read `conversations.json` or split `conversations-*.json` files
containing an array of conversations with a `mapping` of message nodes. The
synthetic fixture shows the supported shape. Python 3.12 is the CI-tested runtime.

Reports include titles, activity patterns, keywords and model names derived from
your export. The generators read local files and write HTML and JSON locally.
Generated HTML includes its styles and interaction code, without external
scripts, fonts or analytics. Conversation-derived text is HTML-escaped.
Review both outputs before sharing them; escaping protects the page structure,
but does not remove personal information. Keep real exports and reports in
ignored folders.

Topic and language labels come from keyword rules, and word counts depend on
the text saved in the export. They are approximate descriptions, not an
assessment of a person's interests or a complete record of account usage.

## Validate

```bash
python -m pytest
python -m py_compile extract_chatgpt_markdown.py generate_report.py generate_wrapped.py
```

CI runs these checks and both synthetic examples. Actions use pinned commits
and read-only repository permissions. Renovate pins development dependencies,
waits seven days for ordinary releases, and keeps major updates for review.
Eligible non-major updates merge after required checks pass; security updates
bypass the release-age delay. Require the `test` check before enabling Renovate
automerge.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
