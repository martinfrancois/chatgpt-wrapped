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

## Validate

```bash
python -m pytest
python -m py_compile extract_chatgpt_markdown.py generate_report.py generate_wrapped.py
```
