from __future__ import annotations

import json
import html
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest


def message(
    node_id: str,
    role: str,
    text: str,
    *,
    parent: str | None,
    children: list[str] | None = None,
    timestamp: int,
) -> dict[str, object]:
    return {
        "id": node_id,
        "parent": parent,
        "children": children or [],
        "message": {
            "id": node_id,
            "author": {"role": role},
            "create_time": timestamp,
            "update_time": timestamp,
            "status": "finished_successfully",
            "channel": "final",
            "content": {"content_type": "text", "parts": [text]},
        },
    }


def write_synthetic_export(export_dir: Path) -> None:
    conversation = {
        "title": "Synthetic dashboard planning",
        "id": "synthetic-conversation",
        "conversation_id": "synthetic-conversation",
        "current_node": "assistant-2",
        "create_time": 1_735_689_600,
        "update_time": 1_735_689_780,
        "default_model_slug": "synthetic-model",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user-1"], "message": None},
            "user-1": message(
                "user-1",
                "user",
                "Please build a Python report with charts and https://example.test/docs.",
                parent="root",
                children=["assistant-1"],
                timestamp=1_735_689_600,
            ),
            "assistant-1": message(
                "assistant-1",
                "assistant",
                "Use a ```python\\nprint('chart')\\n``` block and a compact layout.",
                parent="user-1",
                children=["user-2"],
                timestamp=1_735_689_660,
            ),
            "user-2": message(
                "user-2",
                "user",
                "Add a yearly topic comparison and keep the wording friendly.",
                parent="assistant-1",
                children=["assistant-2"],
                timestamp=1_735_689_720,
            ),
            "assistant-2": message(
                "assistant-2",
                "assistant",
                "The report now includes yearly topics, code languages, and linked sites.",
                parent="user-2",
                timestamp=1_735_689_780,
            ),
        },
    }
    export_dir.mkdir()
    (export_dir / "conversations-000.json").write_text(
        json.dumps([conversation]), encoding="utf-8"
    )


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def test_generators_create_html_and_json_from_synthetic_export(tmp_path: Path) -> None:
    # Given
    repo_root = Path(__file__).resolve().parents[1]
    export_dir = tmp_path / "Conversations"
    write_synthetic_export(export_dir)
    report_out = tmp_path / "report"
    wrapped_out = tmp_path / "wrapped"

    # When
    run_command(
        [
            sys.executable,
            "-m",
            "generate_report",
            str(export_dir),
            "--output-dir",
            str(report_out),
            "--timezone",
            "UTC",
        ],
        repo_root,
    )
    run_command(
        [
            sys.executable,
            "-m",
            "generate_wrapped",
            str(export_dir),
            "--output-dir",
            str(wrapped_out),
            "--timezone",
            "UTC",
        ],
        repo_root,
    )

    # Then
    report_html = report_out / "chatgpt-conversation-report.html"
    report_stats = report_out / "chatgpt-conversation-report-stats.json"
    wrapped_html = wrapped_out / "chatgpt-wrapped-all-time.html"
    wrapped_stats = wrapped_out / "chatgpt-wrapped-all-time-stats.json"

    assert report_html.exists()
    assert report_stats.exists()
    assert wrapped_html.exists()
    assert wrapped_stats.exists()
    assert "Year-by-year comparisons" in report_html.read_text(encoding="utf-8")
    assert "ChatGPT history, all time" in wrapped_html.read_text(encoding="utf-8")
    assert json.loads(report_stats.read_text(encoding="utf-8"))["conversations"] == 1
    assert json.loads(wrapped_stats.read_text(encoding="utf-8"))["conversations"] == 1


@pytest.mark.parametrize(
    ("module", "filename"),
    [
        ("generate_report", "chatgpt-conversation-report.html"),
        ("generate_wrapped", "chatgpt-wrapped-all-time.html"),
    ],
)
def test_reports_escape_export_text_and_load_no_external_resources(
    tmp_path: Path, module: str, filename: str
) -> None:
    # Given
    repo_root = Path(__file__).resolve().parents[1]
    export_dir = tmp_path / "Conversations"
    write_synthetic_export(export_dir)
    source = export_dir / "conversations-000.json"
    conversations = json.loads(source.read_text(encoding="utf-8"))
    payload = (
        '</script><script>window.fixtureExecuted = true</script>'
        '<img src="https://example.test/tracker" onerror="alert(1)">'
    )
    conversations[0]["title"] = payload
    conversations[0]["default_model_slug"] = payload
    source.write_text(json.dumps(conversations), encoding="utf-8")
    output_dir = tmp_path / "output"

    # When
    run_command(
        [sys.executable, "-m", module, str(export_dir),
         "--output-dir", str(output_dir), "--timezone", "UTC"],
        repo_root,
    )
    document = (output_dir / filename).read_text(encoding="utf-8")

    # Then
    assert payload not in document
    assert html.escape(payload, quote=True) in document

    class ReportParser(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            assert tag not in {"img", "iframe", "object", "embed"}
            for name, _ in attrs:
                assert not name.startswith("on")
                assert name not in {"src", "href", "xlink:href"}

    ReportParser().feed(document)
