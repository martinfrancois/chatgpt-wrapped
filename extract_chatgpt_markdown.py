#!/usr/bin/env python3
"""Extract ChatGPT export conversations to Markdown."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


OutputMode = Literal["both", "normal", "reasoning"]
REASONING_CONTENT_TYPES = {"reasoning_recap", "thoughts"}
KNOWN_CONTENT_TYPES = {"text", "multimodal_text", "code", "reasoning_recap", "thoughts"}
KNOWN_MARKER_KINDS = {
    "businesses_map",
    "cite",
    "cite…",
    "entity",
    "filecite",
    "finance",
    "forecast",
    "i",
    "image_group",
    "link",
    "link_title",
    "navlist",
    "product",
    "product_entity",
    "products",
    "summary",
    "tlwm",
    "video",
    "web.run",
}
KNOWN_REFERENCE_TYPES = {
    "alt_text",
    "attribution",
    "businesses_map",
    "client_defined_widget",
    "entity",
    "file",
    "forecast",
    "grouped_webpages",
    "grouped_webpages_model_predicted_fallback",
    "hidden",
    "image_group",
    "image_inline",
    "image_v2",
    "link_title",
    "nav_list",
    "navigation",
    "optimistic_image_citation",
    "product",
    "product_entity",
    "products",
    "sources_footnote",
    "stock",
    "tldr",
    "url",
    "video",
    "webpage",
    "webpage_extended",
}
KNOWN_MESSAGE_CHANNELS = {"analysis", "commentary", "final"}
KNOWN_MESSAGE_STATUSES = {
    "finished_partial_completion",
    "finished_successfully",
    "in_progress",
}
KNOWN_PRIVATE_USE_GLYPH_REPLACEMENTS = {
    "\ue0ac": "Email:",
    "\ue203": "",
    "\ue204": "",
    "\ue206": "",
    "\ue288": " - ",
    "\ue316": "Location:",
    "\ue3b8": "Phone:",
    "\uf8ff": "Apple menu",
    "\ue2e2": "Link:",
}
FILENAME_TIMESTAMP_FALLBACK = "0000-00-00T00-00-00.000000Z"
FILENAME_TIMESTAMP_FORMAT = "%Y-%m-%dT%H-%M-%S.%fZ"
TRACKING_QUERY_PARAMETERS = {
    "dclid",
    "fbclid",
    "gclid",
    "gclsrc",
    "gbraid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "srsltid",
    "wbraid",
}
PRIVATE_USE_MARKER_RE: re.Pattern[str] = re.compile(r"[\ue000-\uf8ff]")
MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
CHATGPT_MARKER_RE: re.Pattern[str] = re.compile(
    r"\ue200(?P<kind>[^\ue202\ue201]+)(?P<payload>(?:\ue202[^\ue201]*)?)\ue201"
)
ALT_TERMINATED_CHATGPT_MARKER_RE: re.Pattern[str] = re.compile(
    r"\ue200(?P<kind>[^\s\ue201\ue202\ue20b]+)"
    r"(?P<payload>(?:\ue202[^\ue200\ue201\ue20b]*)+)\ue20b"
)
TRUNCATED_CHATGPT_MARKER_RE: re.Pattern[str] = re.compile(
    r"\ue200(?P<kind>[^\s\ue201\ue202\ue20b]+)"
    r"(?P<payload>(?:\ue202[^\s\ue200\ue201\ue202\ue20b]*)?)(?=$|\s)"
)
ORPHAN_CHATGPT_MARKER_RE: re.Pattern[str] = re.compile(
    r"\ue202[^\ue200\ue201\ue202\ue20b\n]{0,200}[\ue201\ue20b]"
)


@dataclass(frozen=True)
class ValidationIssue:
    source_file: str
    code: str
    detail: str
    conversation_index: int | None = None
    conversation_id: str = ""
    node_id: str = ""
    message_id: str = ""

    def location(self) -> str:
        parts = [self.source_file]
        if self.conversation_index is not None:
            parts.append(f"conversation[{self.conversation_index}]")
        if self.conversation_id:
            parts.append(f"conversation_id={self.conversation_id}")
        if self.node_id:
            parts.append(f"node={self.node_id}")
        if self.message_id:
            parts.append(f"message={self.message_id}")
        return " ".join(parts)


@dataclass
class ValidationReport:
    files_scanned: int = 0
    conversations_scanned: int = 0
    nodes_scanned: int = 0
    messages_scanned: int = 0
    content_types: dict[str, int] = field(default_factory=dict)
    marker_kinds: dict[str, int] = field(default_factory=dict)
    reference_types: dict[str, int] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_issue(
        self,
        source_file: Path,
        code: str,
        detail: str,
        conversation_index: int | None = None,
        conversation_id: str = "",
        node_id: str = "",
        message_id: str = "",
    ) -> None:
        self.issues.append(
            ValidationIssue(
                source_file=source_file.name,
                code=code,
                detail=detail,
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=message_id,
            )
        )


class ExportValidationError(Exception):
    def __init__(self, report: ValidationReport) -> None:
        self.report: ValidationReport = report
        super().__init__(validation_error_summary(report))
ATX_HEADING_RE: re.Pattern[str] = re.compile(r"^(#{1,6})([ \t].*)$")
BLOCKQUOTE_ATX_HEADING_RE: re.Pattern[str] = re.compile(r"^((?:>[ \t]?)+)(#{1,6})([ \t].*)$")
LIST_ATX_HEADING_RE: re.Pattern[str] = re.compile(r"^(\s*(?:[-*+]|\d+[.)])[ \t]+)(#{1,6})([ \t].*)$")
FENCE_START_RE: re.Pattern[str] = re.compile(r"^([`~]{3,})")
RAW_CODE_LINE_RE: re.Pattern[str] = re.compile(
    r"""(?x)
    ^\s*(
        \#!
        |\#\s+\S
        |//\s*\S
        |/\*
        |\*
        |def\s+\w+
        |class\s+\w+
        |from\s+\w+
        |import\s+\w+
        |if\s+.+:
        |elif\s+.+:
        |else:
        |for\s+.+:
        |while\s+.+:
        |try:
        |except\b.*:
        |finally:
        |with\s+.+:
        |return\b
        |print\s*\(
        |[A-Za-z_][A-Za-z0-9_]*\s*=
        |(?:const|let|var)\s+\w+
        |function\s+\w+
        |(?:sudo\s+)?(?:apt|dnf|zypper|brew|npm|pnpm|yarn|python|python3|pip|pip3|podman|docker|git)\b
        |echo\s+
        |export\s+\w+=
    )
    """
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Search or export ChatGPT data-export conversation JSON files and write "
            "conversation branches as Markdown files."
        )
    )
    parser.add_argument(
        "conversations_folder",
        type=Path,
        help="Path to the folder containing conversations.json or conversations-*.json files.",
    )
    parser.add_argument(
        "search_string",
        nargs="?",
        help="Exact string to search for inside conversation message content. Omit with --all.",
    )
    parser.add_argument(
        "output_folder",
        nargs="?",
        type=Path,
        help="Folder where extracted Markdown files should be written for search mode.",
    )
    parser.add_argument(
        "--output-folder",
        dest="named_output_folder",
        type=Path,
        help="Folder where extracted Markdown files should be written. Required with --all.",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="Match search_string case-insensitively.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Export every conversation branch instead of searching for a string.",
    )
    parser.add_argument(
        "--output-mode",
        choices=("both", "normal", "reasoning"),
        default="both",
        help=(
            "Which transcript files to write: both normal and reasoning files, "
            "normal files only, or reasoning files only. Defaults to both."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the export format without writing Markdown files.",
    )
    return parser.parse_args()


def iter_conversation_files(folder: Path) -> list[Path]:
    files = sorted(folder.glob("conversations-*.json"))
    single_file = folder / "conversations.json"
    if single_file.exists():
        files.insert(0, single_file)
    return files


def as_string_keyed_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        result[key] = item
    return result


def string_keyed_dicts(items: Iterable[object]) -> list[dict[str, object]]:
    return [
        item_dict
        for item in items
        if (item_dict := as_string_keyed_dict(item)) is not None
    ]


def load_conversations(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        data: object = json.load(handle)
    if isinstance(data, list):
        return string_keyed_dicts(data)

    data_dict = as_string_keyed_dict(data)
    if data_dict is not None:
        conversations = data_dict.get("conversations")
        if isinstance(conversations, list):
            return string_keyed_dicts(conversations)
        return [data_dict]
    return []


def increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def conversation_id_for_validation(conversation: dict[str, object]) -> str:
    conversation_id = conversation.get("conversation_id") or conversation.get("id")
    return conversation_id if isinstance(conversation_id, str) else ""


def validation_conversations(
    source_file: Path, data: object, report: ValidationReport
) -> list[dict[str, object]]:
    if isinstance(data, list):
        return validation_dict_items(source_file, data, report)

    data_dict = as_string_keyed_dict(data)
    if data_dict is None:
        report.add_issue(
            source_file,
            "top_level_shape",
            "Expected a conversation list, a conversation object, or an object with a conversations list.",
        )
        return []

    conversations = data_dict.get("conversations")
    if isinstance(conversations, list):
        return validation_dict_items(source_file, conversations, report)
    if conversations is not None:
        report.add_issue(
            source_file,
            "conversations_shape",
            "The conversations field exists but is not a list.",
        )
        return []
    return [data_dict]


def validation_dict_items(
    source_file: Path, items: list[object], report: ValidationReport
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, item in enumerate(items):
        item_dict = as_string_keyed_dict(item)
        if item_dict is None:
            report.add_issue(
                source_file,
                "conversation_shape",
                f"Conversation item {index} is not an object with string keys.",
                conversation_index=index,
            )
            continue
        result.append(item_dict)
    return result


def validate_optional_timestamp(
    value: object,
    field_name: str,
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
    node_id: str = "",
    message_id: str = "",
) -> None:
    if value is None or isinstance(value, (int, float)):
        return
    report.add_issue(
        source_file,
        "timestamp_shape",
        f"{field_name} must be numeric when present.",
        conversation_index=conversation_index,
        conversation_id=conversation_id,
        node_id=node_id,
        message_id=message_id,
    )


def validate_marker_kind(
    kind: str,
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
    node_id: str,
    message_id: str,
) -> None:
    increment_count(report.marker_kinds, kind)
    if kind not in KNOWN_MARKER_KINDS:
        report.add_issue(
            source_file,
            "unknown_marker_kind",
            f"Unknown ChatGPT marker kind: {kind}",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id,
        )


def remove_known_private_use_marker_forms(text: str) -> str:
    without_markers = CHATGPT_MARKER_RE.sub("", text)
    without_markers = ALT_TERMINATED_CHATGPT_MARKER_RE.sub("", without_markers)
    without_markers = TRUNCATED_CHATGPT_MARKER_RE.sub("", without_markers)
    return ORPHAN_CHATGPT_MARKER_RE.sub("", without_markers)


def normalize_known_private_use_glyphs(text: str) -> str:
    for glyph, replacement in KNOWN_PRIVATE_USE_GLYPH_REPLACEMENTS.items():
        text = text.replace(glyph, replacement)
    return text


def validate_private_use_markers(
    text: str,
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
    node_id: str,
    message_id: str,
) -> None:
    remaining = normalize_known_private_use_glyphs(
        remove_known_private_use_marker_forms(text)
    )
    if PRIVATE_USE_MARKER_RE.search(remaining) is not None:
        report.add_issue(
            source_file,
            "malformed_marker",
            "Text contains private-use marker glyphs that do not match a known marker form.",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id,
        )

    text_without_full_markers = CHATGPT_MARKER_RE.sub("", text)
    for match in CHATGPT_MARKER_RE.finditer(text):
        validate_marker_kind(
            match.group("kind"),
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
            message_id,
        )
    text_without_alt_markers = ALT_TERMINATED_CHATGPT_MARKER_RE.sub(
        "", text_without_full_markers
    )
    for match in ALT_TERMINATED_CHATGPT_MARKER_RE.finditer(text_without_full_markers):
        validate_marker_kind(
            match.group("kind"),
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
            message_id,
        )
    for match in TRUNCATED_CHATGPT_MARKER_RE.finditer(text_without_alt_markers):
        validate_marker_kind(
            match.group("kind"),
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
            message_id,
        )


def validate_content_references(
    metadata: dict[str, object],
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
    node_id: str,
    message_id: str,
) -> None:
    references = metadata.get("content_references")
    if references is None:
        return
    if not isinstance(references, list):
        report.add_issue(
            source_file,
            "content_references_shape",
            "content_references must be a list when present.",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id,
        )
        return

    for reference in references:
        reference_dict = as_string_keyed_dict(reference)
        if reference_dict is None:
            report.add_issue(
                source_file,
                "content_reference_shape",
                "A content reference is not an object with string keys.",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=message_id,
            )
            continue
        reference_type = reference_dict.get("type")
        if isinstance(reference_type, str):
            increment_count(report.reference_types, reference_type)
            if reference_type not in KNOWN_REFERENCE_TYPES:
                report.add_issue(
                    source_file,
                    "unknown_reference_type",
                    f"Unknown content reference type: {reference_type}",
                    conversation_index=conversation_index,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=message_id,
                )


def validate_message(
    message: dict[str, object],
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
    node_id: str,
) -> None:
    report.messages_scanned += 1
    message_id = message.get("id")
    message_id_text = message_id if isinstance(message_id, str) else ""

    content = message_content(message)
    if content is None:
        report.add_issue(
            source_file,
            "message_content_shape",
            "Message content must be an object with string keys.",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id_text,
        )
        return

    content_type = content.get("content_type")
    if not isinstance(content_type, str):
        report.add_issue(
            source_file,
            "content_type_shape",
            "Message content_type must be a string.",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id_text,
        )
    else:
        increment_count(report.content_types, content_type)
        if content_type not in KNOWN_CONTENT_TYPES:
            report.add_issue(
                source_file,
                "unknown_content_type",
                f"Unknown message content_type: {content_type}",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=message_id_text,
            )

    channel = message.get("channel")
    if channel is not None and (not isinstance(channel, str) or channel not in KNOWN_MESSAGE_CHANNELS):
        report.add_issue(
            source_file,
            "unknown_channel",
            f"Unknown message channel: {channel}",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id_text,
        )

    status = message.get("status")
    if not isinstance(status, str) or status not in KNOWN_MESSAGE_STATUSES:
        report.add_issue(
            source_file,
            "unknown_status",
            f"Unknown message status: {status}",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
            node_id=node_id,
            message_id=message_id_text,
        )

    validate_optional_timestamp(
        message.get("create_time"),
        "message.create_time",
        source_file,
        report,
        conversation_index,
        conversation_id,
        node_id,
        message_id_text,
    )
    validate_optional_timestamp(
        message.get("update_time"),
        "message.update_time",
        source_file,
        report,
        conversation_index,
        conversation_id,
        node_id,
        message_id_text,
    )

    for text in iter_strings(content):
        validate_private_use_markers(
            text,
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
            message_id_text,
        )

    metadata = message_metadata(message)
    if metadata is not None:
        validate_content_references(
            metadata,
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
            message_id_text,
        )


def validate_node_graph(
    mapping: dict[str, object],
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
    conversation_id: str,
) -> None:
    for node_id, node in mapping.items():
        node_object = as_string_keyed_dict(node)
        if node_object is None:
            report.add_issue(
                source_file,
                "node_shape",
                "Node is not an object with string keys.",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
            )
            continue

        report.nodes_scanned += 1
        children = node_object.get("children")
        if not isinstance(children, list):
            report.add_issue(
                source_file,
                "children_shape",
                "Node children must be a list.",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
            )
        else:
            for child in children:
                if not isinstance(child, str) or child not in mapping:
                    report.add_issue(
                        source_file,
                        "child_reference",
                        f"Node child reference is missing or not a string: {child}",
                        conversation_index=conversation_index,
                        conversation_id=conversation_id,
                        node_id=node_id,
                    )

        parent = node_object.get("parent")
        if parent is not None and (not isinstance(parent, str) or parent not in mapping):
            report.add_issue(
                source_file,
                "parent_reference",
                f"Node parent reference is missing or not a string: {parent}",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
            )

        message = node_object.get("message")
        if message is None:
            continue
        message_dict = as_string_keyed_dict(message)
        if message_dict is None:
            report.add_issue(
                source_file,
                "message_shape",
                "Node message must be null or an object with string keys.",
                conversation_index=conversation_index,
                conversation_id=conversation_id,
                node_id=node_id,
            )
            continue
        validate_message(
            message_dict,
            source_file,
            report,
            conversation_index,
            conversation_id,
            node_id,
        )


def validate_conversation(
    conversation: dict[str, object],
    source_file: Path,
    report: ValidationReport,
    conversation_index: int,
) -> None:
    report.conversations_scanned += 1
    conversation_id = conversation_id_for_validation(conversation)
    validate_optional_timestamp(
        conversation.get("create_time"),
        "conversation.create_time",
        source_file,
        report,
        conversation_index,
        conversation_id,
    )
    validate_optional_timestamp(
        conversation.get("update_time"),
        "conversation.update_time",
        source_file,
        report,
        conversation_index,
        conversation_id,
    )

    mapping = as_string_keyed_dict(conversation.get("mapping"))
    if mapping is None:
        report.add_issue(
            source_file,
            "mapping_shape",
            "Conversation mapping must be an object with string keys.",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
        )
        return

    current_node = conversation.get("current_node")
    if current_node is not None and (not isinstance(current_node, str) or current_node not in mapping):
        report.add_issue(
            source_file,
            "current_node_reference",
            f"current_node is missing or not a string: {current_node}",
            conversation_index=conversation_index,
            conversation_id=conversation_id,
        )

    validate_node_graph(mapping, source_file, report, conversation_index, conversation_id)


def validate_conversation_file(source_file: Path, report: ValidationReport) -> None:
    report.files_scanned += 1
    try:
        with source_file.open("r", encoding="utf-8") as handle:
            data: object = json.load(handle)
    except json.JSONDecodeError as exc:
        report.add_issue(source_file, "json_decode", str(exc))
        return

    for conversation_index, conversation in enumerate(
        validation_conversations(source_file, data, report)
    ):
        validate_conversation(conversation, source_file, report, conversation_index)


def validation_error_summary(report: ValidationReport) -> str:
    issue_lines = [
        f"{issue.location()}: {issue.code}: {issue.detail}"
        for issue in report.issues[:10]
    ]
    remaining = len(report.issues) - len(issue_lines)
    if remaining > 0:
        issue_lines.append(f"... and {remaining} more validation issue(s)")
    return "Export validation failed:\n" + "\n".join(issue_lines)


def validation_summary(report: ValidationReport) -> str:
    return (
        "Export validation passed: "
        f"{report.files_scanned} file(s), "
        f"{report.conversations_scanned} conversation(s), "
        f"{report.nodes_scanned} node(s), "
        f"{report.messages_scanned} message(s), "
        f"{len(report.content_types)} content type(s), "
        f"{len(report.marker_kinds)} marker kind(s), "
        f"{len(report.reference_types)} reference type(s)."
    )


def validate_conversations_folder(conversations_folder: Path) -> ValidationReport:
    conversations_folder = conversations_folder.expanduser().resolve()
    if not conversations_folder.is_dir():
        raise FileNotFoundError(f"Conversations folder not found: {conversations_folder}")

    conversation_files = iter_conversation_files(conversations_folder)
    if not conversation_files:
        raise FileNotFoundError(
            f"No conversations.json or conversations-*.json files found in {conversations_folder}"
        )

    report = ValidationReport()
    for source_file in conversation_files:
        validate_conversation_file(source_file, report)
    if report.issues:
        raise ExportValidationError(report)
    return report


def iter_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)


def message_search_text(message: dict[str, object]) -> str:
    return "\n".join(iter_strings(message.get("content", {})))


def contains(haystack: str, needle: str, ignore_case: bool) -> bool:
    if ignore_case:
        return needle.casefold() in haystack.casefold()
    return needle in haystack


def matching_node_ids(
    mapping: dict[str, object], needle: str, ignore_case: bool
) -> list[str]:
    matches: list[str] = []
    for node_id in mapping:
        message = node_message(mapping, node_id)
        if message is None or not is_user_facing_message(message):
            continue
        if contains(message_search_text(message), needle, ignore_case):
            matches.append(node_id)
    return matches


def node_children(mapping: dict[str, object], node_id: str) -> list[str]:
    node = node_dict(mapping, node_id)
    if node is None:
        return []
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, str) and child in mapping]


def leaf_node_ids(mapping: dict[str, object], current_node: str | None) -> list[str]:
    leaves = [node_id for node_id in mapping if not node_children(mapping, node_id)]
    if not leaves and current_node and current_node in mapping:
        leaves = [current_node]
    return leaves


def path_to_node(mapping: dict[str, object], node_id: str) -> list[str]:
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = node_id
    while current and current in mapping and current not in seen:
        seen.add(current)
        path.append(current)
        node = node_dict(mapping, current)
        parent = node.get("parent") if node is not None else None
        current = parent if isinstance(parent, str) else None
    path.reverse()
    return path


def append_unique_path(
    branches: list[list[str]], seen_paths: set[tuple[str, ...]], path: list[str]
) -> None:
    if not path:
        return
    key = tuple(path)
    if key not in seen_paths:
        seen_paths.add(key)
        branches.append(path)


def matching_branch_paths(
    mapping: dict[str, object],
    current_node: str | None,
    matches: list[str],
) -> list[list[str]]:
    match_set = set(matches)
    branches: list[list[str]] = []
    seen_paths: set[tuple[str, ...]] = set()

    for leaf_id in leaf_node_ids(mapping, current_node):
        path = path_to_node(mapping, leaf_id)
        if not match_set.intersection(path):
            continue
        append_unique_path(branches, seen_paths, path)

    if branches:
        return branches

    for match_id in matches:
        append_unique_path(branches, seen_paths, path_to_node(mapping, match_id))
    return branches


def all_branch_paths(
    mapping: dict[str, object],
    current_node: str | None,
) -> list[list[str]]:
    branches: list[list[str]] = []
    seen_paths: set[tuple[str, ...]] = set()
    for leaf_id in leaf_node_ids(mapping, current_node):
        append_unique_path(branches, seen_paths, path_to_node(mapping, leaf_id))
    return branches


def selected_branch_paths(
    mapping: dict[str, object],
    current_node: str | None,
    search_string: str | None,
    ignore_case: bool,
    export_all: bool,
) -> tuple[list[list[str]], set[str]]:
    if export_all:
        return all_branch_paths(mapping, current_node), set()
    if search_string is None:
        return [], set()

    matches = matching_node_ids(mapping, search_string, ignore_case)
    if not matches:
        return [], set()
    return matching_branch_paths(mapping, current_node, matches), set(matches)


def normalized_timestamp(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if timestamp > 1_000_000_000_000_000:
        return timestamp / 1_000_000_000
    if timestamp > 1_000_000_000_000:
        return timestamp / 1000
    return timestamp


def timestamp_to_iso(value: object) -> str:
    timestamp = normalized_timestamp(value)
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return str(value)


def timestamp_for_filename(value: object) -> str:
    timestamp = normalized_timestamp(value)
    if timestamp is None:
        return FILENAME_TIMESTAMP_FALLBACK
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime(FILENAME_TIMESTAMP_FORMAT)
    except (OverflowError, OSError, ValueError):
        return FILENAME_TIMESTAMP_FALLBACK


def node_dict(mapping: dict[str, object], node_id: str) -> dict[str, object] | None:
    node = as_string_keyed_dict(mapping.get(node_id))
    return node


def node_message(mapping: dict[str, object], node_id: str) -> dict[str, object] | None:
    node = node_dict(mapping, node_id)
    return as_string_keyed_dict(node.get("message")) if node is not None else None


def branch_timestamp_value(
    mapping: dict[str, object],
    branch_path: list[str],
    conversation: dict[str, object],
) -> object:
    if branch_path:
        message = node_message(mapping, branch_path[-1])
        if message is not None:
            for key in ("update_time", "create_time"):
                timestamp = message.get(key)
                if isinstance(timestamp, (int, float)):
                    return timestamp
    return conversation.get("update_time")


def safe_filename_part(value: str, fallback: str = "conversation") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return (value or fallback)[:80].strip("-") or fallback


def markdown_escape_inline(value: str) -> str:
    return value.replace("`", "\\`")


def markdown_escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not is_tracking_query_parameter(key)
    ]
    return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))


def is_tracking_query_parameter(key: str) -> bool:
    normalized = key.lower()
    return normalized.startswith("utm_") or normalized in TRACKING_QUERY_PARAMETERS


def clean_markdown_link_urls(text: str) -> str:
    lines: list[str] = []
    active_fence: str | None = None

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_ending = line[len(content) :]
        active_fence, is_fence_marker = update_fence_state(active_fence, content)
        if active_fence is None and not is_fence_marker:
            content = MARKDOWN_LINK_RE.sub(
                lambda match: f"[{match.group(1)}]({clean_url(match.group(2))})",
                content,
            )
        lines.append(f"{content}{line_ending}")
    return "".join(lines)


def source_label(source: dict[str, object]) -> str:
    title = source.get("title")
    attribution = source.get("attribution")
    title_text = compact_whitespace(title) if isinstance(title, str) else ""
    attribution_text = compact_whitespace(attribution) if isinstance(attribution, str) else ""
    if title_text and attribution_text and title_text != attribution_text:
        return f"{title_text} ({attribution_text})"
    return title_text or attribution_text


def source_markdown_link(source: dict[str, object]) -> str | None:
    url = source.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    clean = clean_url(url.strip())
    label = source_label(source) or clean
    return f"[{markdown_escape_link_text(label)}]({clean})"


def marker_segments(marker: str) -> tuple[str, list[str]] | None:
    match = CHATGPT_MARKER_RE.fullmatch(marker)
    if match is None:
        return None
    payload = match.group("payload")
    segments = payload.split("\ue202")[1:] if payload else []
    return match.group("kind"), segments


def chatgpt_marker(kind: str, segments: list[str]) -> str:
    payload = "".join(f"\ue202{segment}" for segment in segments)
    return f"\ue200{kind}{payload}\ue201"


def entity_marker_replacement(marker: str) -> str:
    parsed = marker_segments(marker)
    if parsed is None:
        return ""
    kind, segments = parsed
    if kind != "entity" or not segments:
        return ""
    payload = segments[0]
    try:
        value: object = json.loads(payload)
    except json.JSONDecodeError:
        return compact_whitespace(payload)

    if isinstance(value, list):
        strings = [item for item in value if isinstance(item, str) and item.strip()]
        if len(strings) >= 2:
            return strings[1].strip()
        if strings:
            return strings[0].strip()
    value_dict = as_string_keyed_dict(value)
    if value_dict is not None:
        for key in ("name", "title", "text", "label"):
            item = value_dict.get(key)
            if isinstance(item, str) and item.strip():
                return compact_whitespace(item)
    return ""


def file_reference_label(reference: dict[str, object]) -> str:
    for key in ("name", "title", "source", "id"):
        value = reference.get(key)
        if isinstance(value, str) and value.strip():
            return compact_whitespace(value)
    return "unknown file"


def readable_line_range(value: str) -> str:
    if re.fullmatch(r"L\d+(?:-L?\d+)?", value) is None:
        return compact_whitespace(value)
    return f"lines {value.removeprefix('L').replace('-L', '-')}"


def file_reference_replacement(
    reference: dict[str, object], line_ranges: list[str] | None = None
) -> str:
    details = [f"File: `{markdown_escape_inline(file_reference_label(reference))}`"]
    if line_ranges:
        details.append(", ".join(readable_line_range(line_range) for line_range in line_ranges))
    return f"({', '.join(details)})"


def iter_citation_sources(reference: dict[str, object]) -> Iterable[dict[str, object]]:
    items = reference.get("items")
    if isinstance(items, list):
        for item in items:
            item_dict = as_string_keyed_dict(item)
            if item_dict is None:
                continue
            yield item_dict
            supporting_websites = item_dict.get("supporting_websites")
            if isinstance(supporting_websites, list):
                for website in supporting_websites:
                    website_dict = as_string_keyed_dict(website)
                    if website_dict is not None:
                        yield website_dict

    sources = reference.get("sources")
    if isinstance(sources, list):
        for source in sources:
            source_dict = as_string_keyed_dict(source)
            if source_dict is not None:
                yield source_dict


def citation_replacement(reference: dict[str, object]) -> str:
    reference_type = reference.get("type")
    if reference_type == "file":
        return file_reference_replacement(reference)

    links: list[str] = []
    seen_urls: set[str] = set()
    for source in iter_citation_sources(reference):
        link = source_markdown_link(source)
        url = source.get("url")
        clean = clean_url(url.strip()) if isinstance(url, str) else ""
        if link is not None and clean not in seen_urls:
            seen_urls.add(clean)
            links.append(link)

    if links:
        return f"({'; '.join(links)})"

    alt = reference.get("alt")
    if isinstance(alt, str) and alt.strip():
        return alt.strip()
    return ""


def citation_replacements(message: dict[str, object]) -> dict[str, str]:
    metadata = message_metadata(message)
    if metadata is None:
        return {}
    references = metadata.get("content_references")
    if not isinstance(references, list):
        return {}

    replacements: dict[str, str] = {}
    for reference in references:
        reference_dict = as_string_keyed_dict(reference)
        if reference_dict is None:
            continue
        matched_text = reference_dict.get("matched_text")
        if not isinstance(matched_text, str) or CHATGPT_MARKER_RE.fullmatch(matched_text) is None:
            continue
        replacement = citation_replacement(reference_dict)
        if replacement:
            replacements[matched_text] = replacement
            parsed = marker_segments(matched_text)
            if parsed is not None:
                kind, segments = parsed
                if kind == "filecite" and segments:
                    base_marker = chatgpt_marker("filecite", [segments[0]])
                    replacements.setdefault(base_marker, replacement)
    return replacements


def append_line_ranges_to_replacement(replacement: str, line_ranges: list[str]) -> str:
    if not line_ranges or not replacement.endswith(")"):
        return replacement
    readable_ranges = ", ".join(readable_line_range(line_range) for line_range in line_ranges)
    return f"{replacement[:-1]}, {readable_ranges})"


def replace_citation_markers(text: str, message: dict[str, object]) -> str:
    replacements = citation_replacements(message)

    def replace_match(match: re.Match[str]) -> str:
        marker = match.group(0)
        replacement = replacements.get(marker)
        if replacement is not None:
            return replacement

        parsed = marker_segments(marker)
        if parsed is None:
            return ""
        kind, segments = parsed
        if kind == "entity":
            return entity_marker_replacement(marker)
        if kind == "filecite" and segments:
            base_marker = chatgpt_marker("filecite", [segments[0]])
            file_replacement = replacements.get(base_marker)
            if file_replacement is not None:
                return append_line_ranges_to_replacement(file_replacement, segments[1:])
        return ""

    replaced = CHATGPT_MARKER_RE.sub(replace_match, text)
    replaced = ALT_TERMINATED_CHATGPT_MARKER_RE.sub("", replaced)
    replaced = TRUNCATED_CHATGPT_MARKER_RE.sub("", replaced)
    replaced = ORPHAN_CHATGPT_MARKER_RE.sub("", replaced)
    return normalize_known_private_use_glyphs(replaced)


def update_fence_state(active_fence: str | None, line: str) -> tuple[str | None, bool]:
    fence_match = FENCE_START_RE.match(line)
    if fence_match is None:
        return active_fence, False

    fence = fence_match.group(1)
    if active_fence is None:
        return fence, True
    if fence.startswith(active_fence[0]) and len(fence) >= len(active_fence):
        return None, True
    return active_fence, True


def looks_like_raw_code(text: str) -> bool:
    nonblank_lines: list[str] = []
    active_fence: str | None = None
    for line in text.splitlines():
        active_fence, is_fence_marker = update_fence_state(active_fence, line)
        if is_fence_marker:
            continue
        if active_fence is None and line.strip():
            nonblank_lines.append(line)

    if not nonblank_lines:
        return False

    first_content = nonblank_lines[0].lstrip()
    if first_content.startswith("#!"):
        return True

    if len(nonblank_lines) < 2:
        return False

    code_like_lines = sum(1 for line in nonblank_lines if RAW_CODE_LINE_RE.match(line))
    return code_like_lines >= max(2, (len(nonblank_lines) + 1) // 2)


def demoted_hashes(hashes: str, levels: int) -> str:
    return "#" * min(6, len(hashes) + levels)


def demote_heading_line(content: str, levels: int) -> str:
    for pattern in (BLOCKQUOTE_ATX_HEADING_RE, LIST_ATX_HEADING_RE):
        match = pattern.match(content)
        if match is not None:
            prefix = match.group(1)
            hashes = match.group(2)
            suffix = match.group(3)
            return f"{prefix}{demoted_hashes(hashes, levels)}{suffix}"

    match = ATX_HEADING_RE.match(content)
    if match is None:
        return content
    hashes = match.group(1)
    suffix = match.group(2)
    return f"{demoted_hashes(hashes, levels)}{suffix}"


def demote_markdown_headings(text: str, levels: int = 2) -> str:
    if looks_like_raw_code(text):
        return text

    lines: list[str] = []
    active_fence: str | None = None

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_ending = line[len(content) :]
        active_fence, is_fence_marker = update_fence_state(active_fence, content)
        if is_fence_marker:
            lines.append(line)
            continue

        if active_fence is None:
            content = demote_heading_line(content, levels)

        lines.append(f"{content}{line_ending}")

    return "".join(lines)


def active_markdown_fence(text: str) -> tuple[str, int] | None:
    active_fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if not (stripped.startswith("```") or stripped.startswith("~~~")):
            continue
        run = stripped.split(maxsplit=1)[0]
        marker = run[0]
        length = len(run) - len(run.lstrip(marker))
        if active_fence is None:
            active_fence = (marker, length)
        elif marker == active_fence[0] and length >= active_fence[1]:
            active_fence = None
    return active_fence


def close_unclosed_markdown_fence(text: str) -> str:
    active_fence = active_markdown_fence(text)
    if active_fence is None:
        return text
    marker, length = active_fence
    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}{marker * length}"


def prepare_message_body(text: str, message: dict[str, object]) -> str:
    return close_unclosed_markdown_fence(
        demote_markdown_headings(
            clean_markdown_link_urls(replace_citation_markers(text, message))
        )
    )


def fence_for(text: str) -> str:
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    return "`" * max(3, (max(runs) + 1) if runs else 3)


def fenced_block(text: str, language: str = "") -> str:
    fence = fence_for(text)
    language = re.sub(r"[^A-Za-z0-9_+.#-]", "", language or "")
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def strings_from_mapping(
    value: dict[str, object], excluded_keys: set[str] | None = None
) -> list[str]:
    excluded = excluded_keys or set()
    return [
        text
        for key, item in value.items()
        if key not in excluded
        for text in iter_strings(item)
    ]


def rendered_parts(parts: list[object]) -> list[str]:
    rendered: list[str] = []
    for part in parts:
        text = render_part(part).strip()
        if text:
            rendered.append(text)
    return rendered


def render_part(part: object) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    part_dict = as_string_keyed_dict(part)
    if part_dict is not None:
        for key in ("text", "transcript", "caption", "alt_text"):
            value = part_dict.get(key)
            if isinstance(value, str):
                return value
        asset_pointer = part_dict.get("asset_pointer")
        if isinstance(asset_pointer, str):
            return f"[Attachment: {asset_pointer}]"
        file_id = part_dict.get("file_id")
        if isinstance(file_id, str):
            return f"[File: {file_id}]"
        return "\n".join(strings_from_mapping(part_dict, {"content_type"}))
    if isinstance(part, list):
        return "\n".join(render_part(item) for item in part).strip()
    return str(part)


def message_content(message: dict[str, object]) -> dict[str, object] | None:
    return as_string_keyed_dict(message.get("content"))


def message_metadata(message: dict[str, object]) -> dict[str, object] | None:
    return as_string_keyed_dict(message.get("metadata"))


def is_user_facing_message(message: dict[str, object]) -> bool:
    content = message_content(message)
    content_type = content.get("content_type") if content is not None else None
    if isinstance(content_type, str) and content_type in REASONING_CONTENT_TYPES:
        return False

    metadata = message_metadata(message)
    if metadata is not None and metadata.get("is_visually_hidden_from_conversation") is True:
        return False

    return True


def message_content_type(message: dict[str, object]) -> str:
    content = message_content(message)
    content_type = content.get("content_type") if content is not None else None
    return content_type if isinstance(content_type, str) else ""


def message_channel(message: dict[str, object]) -> str:
    channel = message.get("channel")
    return channel if isinstance(channel, str) else ""


def message_status(message: dict[str, object]) -> str:
    status = message.get("status")
    return status if isinstance(status, str) else ""


def message_to_markdown(message: dict[str, object]) -> str:
    content = message_content(message)
    if content is None:
        return prepare_message_body(render_part(message.get("content")).strip(), message)

    content_type = content.get("content_type")
    parts = content.get("parts")

    if content_type == "code":
        text = content.get("text")
        if not isinstance(text, str) and isinstance(parts, list):
            text = "\n".join(render_part(part) for part in parts)
        if isinstance(text, str):
            return prepare_message_body(
                fenced_block(text, str(content.get("language") or "")).strip(),
                message,
            )

    if isinstance(parts, list):
        body = "\n\n".join(rendered_parts(parts))
        return prepare_message_body(body, message)

    if isinstance(parts, str):
        return prepare_message_body(parts.strip(), message)

    for key in ("text", "result"):
        value = content.get(key)
        if isinstance(value, str):
            return prepare_message_body(value.strip(), message)

    return prepare_message_body(
        "\n".join(strings_from_mapping(content, {"content_type"})).strip(),
        message,
    )


def role_label(message: dict[str, object]) -> str:
    author = as_string_keyed_dict(message.get("author"))
    if author is None:
        return "Message"
    role = str(author.get("role") or "message").strip() or "message"
    name = str(author.get("name") or "").strip()
    label = role.replace("_", " ").title()
    if name and name != role:
        label = f"{label} ({name})"
    return label


def branch_header_lines(
    conversation: dict[str, object],
    source_file: Path,
    branch_index: int,
    branch_count: int,
    path: list[str],
    matching_ids: list[str],
    search_string: str | None,
    include_reasoning: bool,
) -> list[str]:
    title = str(conversation.get("title") or "Untitled conversation")
    conversation_id = str(conversation.get("conversation_id") or conversation.get("id") or "")
    current_node = conversation.get("current_node")
    leaf_id = path[-1] if path else ""
    lines = [
        f"# {title}",
        "",
        f"- Conversation ID: `{conversation_id}`",
        f"- Source file: `{source_file.name}`",
        f"- Branch: `{branch_index} of {branch_count}`",
        f"- Leaf node: `{leaf_id}`",
        f"- Current branch: `{'yes' if leaf_id == current_node else 'no'}`",
        f"- Conversation created: `{timestamp_to_iso(conversation.get('create_time'))}`",
        f"- Conversation updated: `{timestamp_to_iso(conversation.get('update_time'))}`",
        f"- Matching message IDs: `{', '.join(matching_ids)}`",
        f"- Reasoning traces included: `{'yes' if include_reasoning else 'no'}`",
        "",
        "---",
        "",
    ]
    if search_string is not None:
        lines.insert(10, f"- Search string: `{markdown_escape_inline(search_string)}`")
    return lines


def reasoning_notice_lines() -> list[str]:
    return [
        "> [!IMPORTANT]",
        "> This file includes reasoning and trace entries from the export.",
        "> Reasoning and trace entries are marked `[REASONING TRACE]`.",
        "",
    ]


def output_message(
    mapping: dict[str, object], node_id: str, include_reasoning: bool
) -> tuple[dict[str, object], bool, str] | None:
    message = node_message(mapping, node_id)
    if message is None:
        return None
    is_user_facing = is_user_facing_message(message)
    if not include_reasoning and not is_user_facing:
        return None
    body = message_to_markdown(message)
    if not body:
        return None
    return message, is_user_facing, body


def message_heading(
    node_id: str,
    message: dict[str, object],
    is_user_facing: bool,
    match_set: set[str],
    include_reasoning: bool,
) -> str:
    message_time = timestamp_to_iso(message.get("create_time"))
    suffix = f" - {message_time}" if message_time else ""
    match_marker = " [MATCH]" if node_id in match_set else ""
    reasoning_marker = " [REASONING TRACE]" if include_reasoning and not is_user_facing else ""
    return f"## {role_label(message)}{suffix}{match_marker}{reasoning_marker}"


def reasoning_metadata_lines(node_id: str, message: dict[str, object]) -> list[str]:
    return [
        f"- Node ID: `{node_id}`",
        f"- Content type: `{message_content_type(message) or 'unknown'}`",
        f"- Channel: `{message_channel(message) or 'unknown'}`",
        f"- Status: `{message_status(message) or 'unknown'}`",
        "",
        "> Reasoning trace content from the export:",
        "",
    ]


def message_section_lines(
    node_id: str,
    message: dict[str, object],
    is_user_facing: bool,
    body: str,
    match_set: set[str],
    include_reasoning: bool,
) -> list[str]:
    lines = [
        message_heading(node_id, message, is_user_facing, match_set, include_reasoning),
        "",
    ]
    if include_reasoning and not is_user_facing:
        lines.extend(reasoning_metadata_lines(node_id, message))
    lines.extend([body.rstrip(), ""])
    return lines


def render_branch_markdown(
    conversation: dict[str, object],
    source_file: Path,
    branch_index: int,
    branch_count: int,
    path: list[str],
    matching_ids: list[str],
    search_string: str | None,
    include_reasoning: bool,
) -> str:
    mapping = as_string_keyed_dict(conversation.get("mapping")) or {}
    match_set = set(matching_ids)
    lines = branch_header_lines(
        conversation=conversation,
        source_file=source_file,
        branch_index=branch_index,
        branch_count=branch_count,
        path=path,
        matching_ids=matching_ids,
        search_string=search_string,
        include_reasoning=include_reasoning,
    )

    if include_reasoning:
        lines.extend(reasoning_notice_lines())

    for node_id in path:
        output = output_message(mapping, node_id, include_reasoning)
        if output is None:
            continue
        message, is_user_facing, body = output
        lines.extend(
            message_section_lines(
                node_id=node_id,
                message=message,
                is_user_facing=is_user_facing,
                body=body,
                match_set=match_set,
                include_reasoning=include_reasoning,
            )
        )

    return "\n".join(lines).rstrip() + "\n"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create a unique path for {path}")


def branch_matching_ids(branch_path: list[str], matches: set[str]) -> list[str]:
    return [node_id for node_id in branch_path if node_id in matches]


def conversation_current_node(conversation: dict[str, object]) -> str | None:
    current_node = conversation.get("current_node")
    return current_node if isinstance(current_node, str) else None


def branch_filename(
    mapping: dict[str, object],
    conversation: dict[str, object],
    branch_file_index: int,
    branch_index: int,
    branch_path: list[str],
) -> str:
    title = str(conversation.get("title") or "untitled")
    conversation_id = str(conversation.get("conversation_id") or conversation.get("id") or "")
    title_part = safe_filename_part(title)
    id_part = safe_filename_part(conversation_id[:8], "no-id")
    leaf_part = safe_filename_part(branch_path[-1][:8] if branch_path else "", "no-leaf")
    timestamp_part = timestamp_for_filename(
        branch_timestamp_value(mapping, branch_path, conversation)
    )
    return (
        f"{timestamp_part}__{branch_file_index:03d}-{title_part}-{id_part}-"
        f"branch-{branch_index:03d}-{leaf_part}.md"
    )


def write_branch_markdown(
    output_path: Path,
    conversation: dict[str, object],
    source_file: Path,
    branch_index: int,
    branch_count: int,
    branch_path: list[str],
    matching_ids: list[str],
    search_string: str | None,
    include_reasoning: bool,
) -> Path:
    markdown = render_branch_markdown(
        conversation=conversation,
        source_file=source_file,
        branch_index=branch_index,
        branch_count=branch_count,
        path=branch_path,
        matching_ids=matching_ids,
        search_string=search_string,
        include_reasoning=include_reasoning,
    )
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def output_mode_reasoning_flags(output_mode: OutputMode) -> list[bool]:
    if output_mode == "normal":
        return [False]
    if output_mode == "reasoning":
        return [True]
    return [False, True]


def reasoning_output_path(normal_path: Path) -> Path:
    return normal_path.with_name(f"{normal_path.stem}-reasoning{normal_path.suffix}")


def write_branch_outputs(
    output_folder: Path,
    conversation: dict[str, object],
    source_file: Path,
    mapping: dict[str, object],
    branch_file_index: int,
    branch_index: int,
    branch_count: int,
    branch_path: list[str],
    matching_ids: list[str],
    search_string: str | None,
    output_mode: OutputMode,
) -> list[Path]:
    normal_path = unique_path(
        output_folder
        / branch_filename(
            mapping=mapping,
            conversation=conversation,
            branch_file_index=branch_file_index,
            branch_index=branch_index,
            branch_path=branch_path,
        )
    )
    written: list[Path] = []
    for include_reasoning in output_mode_reasoning_flags(output_mode):
        output_path = (
            unique_path(reasoning_output_path(normal_path))
            if include_reasoning
            else normal_path
        )
        written.append(
            write_branch_markdown(
                output_path=output_path,
                conversation=conversation,
                source_file=source_file,
                branch_index=branch_index,
                branch_count=branch_count,
                branch_path=branch_path,
                matching_ids=matching_ids,
                search_string=search_string,
                include_reasoning=include_reasoning,
            )
        )
    return written


def markdown_has_unclosed_fence(text: str) -> bool:
    return active_markdown_fence(text) is not None


def output_file_metadata(text: str, label: str) -> str:
    prefix = f"- {label}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


def validate_markdown_output_files(
    written: list[Path],
    require_match: bool = True,
    output_mode: OutputMode = "both",
) -> None:
    issues: list[str] = []
    normal_files = [path for path in written if not path.name.endswith("-reasoning.md")]

    for path in written:
        is_reasoning_file = path.name.endswith("-reasoning.md")
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            issues.append(f"{path.name}: missing final newline")
        if PRIVATE_USE_MARKER_RE.search(text) is not None:
            issues.append(f"{path.name}: raw ChatGPT marker glyph remains")
        if markdown_has_unclosed_fence(text):
            issues.append(f"{path.name}: unclosed Markdown code fence")
        if require_match and "[MATCH]" not in text:
            issues.append(f"{path.name}: missing [MATCH] marker")
        if output_mode == "normal" and is_reasoning_file:
            issues.append(f"{path.name}: unexpected reasoning file for normal output mode")
        if output_mode == "reasoning" and not is_reasoning_file:
            issues.append(f"{path.name}: unexpected normal file for reasoning output mode")
        if not is_reasoning_file and "[REASONING TRACE]" in text:
            issues.append(f"{path.name}: normal transcript includes reasoning marker")
        if is_reasoning_file and "Reasoning traces included: `yes`" not in text:
            issues.append(f"{path.name}: reasoning transcript missing reasoning header")

    if output_mode == "both":
        for normal_path in normal_files:
            reasoning_path = reasoning_output_path(normal_path)
            if reasoning_path not in written:
                issues.append(f"{normal_path.name}: missing paired reasoning file")
                continue
            normal_text = normal_path.read_text(encoding="utf-8")
            reasoning_text = reasoning_path.read_text(encoding="utf-8")
            for label in ("Conversation ID", "Branch", "Leaf node", "Matching message IDs"):
                if output_file_metadata(normal_text, label) != output_file_metadata(
                    reasoning_text, label
                ):
                    issues.append(f"{normal_path.name}: paired reasoning metadata mismatch: {label}")

    if issues:
        raise RuntimeError("Markdown output validation failed:\n" + "\n".join(issues[:20]))


def write_markdown_files(
    conversations_folder: Path,
    output_folder: Path,
    search_string: str | None,
    ignore_case: bool,
    export_all: bool = False,
    output_mode: OutputMode = "both",
) -> list[Path]:
    conversations_folder = conversations_folder.expanduser().resolve()
    output_folder = output_folder.expanduser().resolve()

    if not export_all and search_string is None:
        raise ValueError("search_string is required unless export_all is true")

    validate_conversations_folder(conversations_folder)
    conversation_files = iter_conversation_files(conversations_folder)

    output_folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    branch_file_index = 0

    for source_file in conversation_files:
        for conversation in load_conversations(source_file):
            mapping = as_string_keyed_dict(conversation.get("mapping"))
            if mapping is None:
                continue

            current_node = conversation_current_node(conversation)
            branches, match_set = selected_branch_paths(
                mapping=mapping,
                current_node=current_node,
                search_string=search_string,
                ignore_case=ignore_case,
                export_all=export_all,
            )
            branch_count = len(branches)
            if branch_count == 0:
                continue

            for branch_index, branch_path in enumerate(branches, start=1):
                branch_file_index += 1
                written.extend(
                    write_branch_outputs(
                        output_folder=output_folder,
                        conversation=conversation,
                        source_file=source_file,
                        mapping=mapping,
                        branch_file_index=branch_file_index,
                        branch_index=branch_index,
                        branch_count=branch_count,
                        branch_path=branch_path,
                        matching_ids=branch_matching_ids(branch_path, match_set),
                        search_string=search_string,
                        output_mode=output_mode,
                    )
                )

    validate_markdown_output_files(
        written, require_match=not export_all, output_mode=output_mode
    )
    return written


def output_mode_from_arg(value: object) -> OutputMode:
    if value == "normal":
        return "normal"
    if value == "reasoning":
        return "reasoning"
    return "both"


def output_folder_from_args(args: argparse.Namespace) -> Path | None:
    named_output_folder = args.named_output_folder
    if isinstance(named_output_folder, Path):
        return named_output_folder
    positional_output_folder = args.output_folder
    return positional_output_folder if isinstance(positional_output_folder, Path) else None


def print_written_files(written: list[Path], export_all: bool) -> None:
    if export_all and written:
        print("File list omitted for --all; use the output folder to inspect results.")
        return
    for path in written:
        print(path)


def main() -> int:
    args = parse_args()
    try:
        if args.validate_only:
            report = validate_conversations_folder(args.conversations_folder)
            print(validation_summary(report))
            return 0
        output_mode = output_mode_from_arg(args.output_mode)
        output_folder = output_folder_from_args(args)
        if args.all:
            if args.search_string is not None or args.output_folder is not None:
                print(
                    "error: --all does not accept positional search_string/output_folder; "
                    "use --output-folder",
                    file=sys.stderr,
                )
                return 1
            if output_folder is None:
                print("error: --output-folder is required with --all", file=sys.stderr)
                return 1
            written = write_markdown_files(
                conversations_folder=args.conversations_folder,
                output_folder=output_folder,
                search_string=None,
                ignore_case=args.ignore_case,
                export_all=True,
                output_mode=output_mode,
            )
        elif args.search_string is None or output_folder is None:
            print(
                "error: search_string and output_folder are required unless --all or "
                "--validate-only is used",
                file=sys.stderr,
            )
            return 1
        else:
            written = write_markdown_files(
                conversations_folder=args.conversations_folder,
                output_folder=output_folder,
                search_string=args.search_string,
                ignore_case=args.ignore_case,
                output_mode=output_mode,
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(written)} Markdown file(s) to {output_folder.expanduser().resolve()}")
    print_written_files(written, export_all=args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
