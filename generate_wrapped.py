from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from extract_chatgpt_markdown import (
    all_branch_paths,
    as_string_keyed_dict,
    clean_url,
    conversation_current_node,
    iter_conversation_files,
    iter_strings,
    load_conversations,
    remove_known_private_use_marker_forms,
    validate_conversations_folder,
)


EXPORT = Path(".")
OUT_DIR = Path("output/chatgpt-wrapped-all-time")
HTML_PATH = OUT_DIR / "chatgpt-wrapped-all-time.html"
STATS_PATH = OUT_DIR / "chatgpt-wrapped-all-time-stats.json"
TZ = ZoneInfo("Europe/Zurich")

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{1,}")
COUNT_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#._-]*")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
FENCE_RE = re.compile(r"^\s*([`~]{3,})([A-Za-z0-9_+.#-]*)", re.MULTILINE)
FENCED_BLOCK_RE = re.compile(r"(^|\n)\s*([`~]{3,}).*?\2", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
HTML_ENTITY_RE = re.compile(r"&[A-Za-z0-9#]+;")

STOPWORDS = {
    "about", "after", "again", "all", "also", "and", "any", "are", "around", "ask",
    "asked", "because", "been", "before", "being", "best", "better", "both",
    "but", "can", "chat", "chatgpt", "could", "create", "did", "does",
    "doing", "done", "each", "else", "even", "every", "example", "file",
    "files", "find", "first", "for", "from", "get", "getting", "give",
    "good", "had", "has", "have", "help", "here", "how", "into", "its",
    "just", "like", "look", "make", "many", "may", "more", "most", "much",
    "need", "needs", "new", "not", "now", "old", "one", "only", "other",
    "out", "over", "please", "possible", "really", "right", "same",
    "should", "some", "something", "sure", "tell", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "thing", "things",
    "this", "those", "through", "today", "use", "used", "using", "want",
    "was", "way", "were", "what", "when", "where", "which", "while",
    "with", "without", "work", "working", "works", "would", "write", "you",
    "your", "youre",
}

CODE_WORDS = {
    "__init__", "async", "await", "boolean", "bool", "class", "classname",
    "const", "constructor", "def", "dict", "div", "elif", "false", "float",
    "function", "html", "https", "impl", "import", "int", "interface", "json",
    "key", "keys", "lambda", "let", "localhost", "nbsp", "none", "null",
    "object", "private", "props", "public", "return", "self", "span", "src",
    "static", "string", "struct", "text", "true", "type", "value", "values",
    "var", "void", "x00",
}

TOPICS = {
    "Software craft": {
        "api", "backend", "bug", "ci", "code", "database", "dependency",
        "frontend", "git", "github", "html", "java", "javascript", "json",
        "node", "package", "python", "react", "repo", "repository", "script",
        "sql", "test", "tests", "typescript", "yaml",
    },
    "AI and agents": {
        "agent", "agents", "assistant", "claude", "codex", "embedding", "gpt",
        "llm", "model", "models", "openai", "prompt", "prompts", "reasoning",
        "tool",
    },
    "Infrastructure": {
        "aws", "azure", "cloud", "container", "containers", "deploy", "dns",
        "docker", "gcp", "helm", "k8s", "kubernetes", "linux", "network",
        "nginx", "podman", "server", "ssh", "terraform",
    },
    "Design and UX": {
        "accessibility", "button", "color", "colors", "component", "contrast",
        "design", "figma", "font", "layout", "style", "tailwind", "theme",
        "ui", "ux",
    },
    "Writing and docs": {
        "article", "copy", "document", "draft", "email", "markdown", "post",
        "readme", "rewrite", "summary", "summarize", "tone", "wording",
    },
    "Products and shopping": {
        "buy", "camera", "compare", "price", "product", "recommend", "review",
        "reviews", "shop", "shopping",
    },
    "Devices and hardware": {
        "battery", "camera", "device", "disk", "display", "drive", "iphone",
        "keyboard", "laptop", "lenovo", "mac", "monitor", "phone", "router",
        "ssd", "usb",
    },
    "Travel and places": {
        "airport", "basel", "city", "flight", "hotel", "map", "restaurant",
        "route", "switzerland", "train", "travel", "trip", "zurich",
    },
    "Personal admin": {
        "account", "appointment", "bank", "budget", "calendar", "insurance",
        "invoice", "plan", "planning", "schedule", "tax", "time",
    },
    "Culture and games": {
        "book", "film", "game", "games", "movie", "music", "series", "show",
        "song",
    },
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def tooltip_attr(value: object) -> str:
    return f'data-tooltip="{esc(value)}" tabindex="0"'


def as_dt(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if timestamp > 1_000_000_000_000_000:
        timestamp /= 1_000_000_000
    elif timestamp > 1_000_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(TZ)
    except (OSError, OverflowError, ValueError):
        return None


def month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def human_int(value: float | int) -> str:
    return f"{int(round(value)):,}"


def nice_ticks(max_value: float | int, target_steps: int = 4) -> list[float]:
    if max_value <= 0:
        return [0.0, 1.0]
    raw_step = float(max_value) / target_steps
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        step = magnitude
    elif residual <= 2:
        step = 2 * magnitude
    elif residual <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    axis_max = step * math.ceil(float(max_value) / step)
    ticks: list[float] = []
    current = 0.0
    while current <= axis_max + step / 2:
        ticks.append(current)
        current += step
    return ticks


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def pct(part: float, total: float) -> float:
    return 0.0 if total == 0 else 100.0 * part / total


def percentile(values: list[float] | list[int], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    idx = (len(ordered) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - idx) + ordered[hi] * (idx - lo)


def words(text: str) -> list[str]:
    result: list[str] = []
    for match in WORD_RE.finditer(text.lower()):
        word = match.group(0).strip("._-")
        if len(word) >= 3 and word not in STOPWORDS and not word.isdigit():
            result.append(word)
    return result


def count_words(text: str) -> int:
    return sum(1 for _ in COUNT_WORD_RE.finditer(text))


def prose_words(text: str) -> list[str]:
    text = FENCED_BLOCK_RE.sub("\n", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = HTML_ENTITY_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    return [word for word in words(text) if word not in CODE_WORDS]


def content_body_strings(content: dict[str, object]) -> list[str]:
    content_type = str(content.get("content_type") or "")
    parts = content.get("parts")
    if content_type in {"text", "multimodal_text"} and isinstance(parts, list):
        strings: list[str] = []
        for part in parts:
            if isinstance(part, str):
                strings.append(part)
                continue
            part_dict = as_string_keyed_dict(part)
            if part_dict is None:
                continue
            for key in ("text", "content", "caption", "transcript"):
                value = part_dict.get(key)
                if isinstance(value, str):
                    strings.append(value)
        return strings
    if content_type == "reasoning_recap":
        value = content.get("content")
        return [value] if isinstance(value, str) else []
    if content_type == "thoughts":
        strings = []
        thoughts = content.get("thoughts")
        if isinstance(thoughts, list):
            for thought in thoughts:
                thought_dict = as_string_keyed_dict(thought)
                if thought_dict is None:
                    continue
                value = thought_dict.get("content")
                if isinstance(value, str):
                    strings.append(value)
        return strings
    return [value for value in iter_strings(content) if value != content_type]


def text_from_content(content: dict[str, object]) -> str:
    return remove_known_private_use_marker_forms("\n".join(content_body_strings(content)))


def clean_domain(url: str) -> str:
    try:
        cleaned = clean_url(url.rstrip(".,;:"))
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    return parsed.netloc.lower().removeprefix("www.")


def iter_reference_urls(reference: dict[str, object]):
    url = reference.get("url")
    if isinstance(url, str):
        yield url
    for key in ("items", "sources", "supporting_websites"):
        items = reference.get(key)
        if isinstance(items, list):
            for item in items:
                item_dict = as_string_keyed_dict(item)
                if item_dict is not None:
                    yield from iter_reference_urls(item_dict)


def active_streak(dates: set[date]) -> tuple[int, date | None, date | None]:
    if not dates:
        return 0, None, None
    ordered = sorted(dates)
    best_len = cur_len = 1
    best_start = cur_start = ordered[0]
    best_end = ordered[0]
    previous = ordered[0]
    for current in ordered[1:]:
        if current == previous + timedelta(days=1):
            cur_len += 1
        else:
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
                best_end = previous
            cur_start = current
            cur_len = 1
        previous = current
    if cur_len > best_len:
        best_len = cur_len
        best_start = cur_start
        best_end = previous
    return best_len, best_start, best_end


def top(counter: Counter[str], n: int) -> list[tuple[str, int]]:
    return [(str(label), int(value)) for label, value in counter.most_common(n)]


def cumulative(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
    total = 0
    rows: list[tuple[str, int]] = []
    for label, value in items:
        total += value
        rows.append((label, total))
    return rows


def bar_chart(items: list[tuple[str, int]], title: str, color: str, width: int = 1040) -> str:
    if not items:
        return '<p class="muted">No data.</p>'
    left = 285
    right = 170
    row_h = 42
    height = 100 + row_h * len(items)
    chart_w = width - left - right
    max_value = max(value for _, value in items) or 1
    axis_ticks = nice_ticks(max_value, 2)
    axis_max = axis_ticks[-1] or 1
    parts = [f'<svg class="chart" role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}">']
    parts.append(f'<text x="0" y="24" class="chart-title">{esc(title)}</text>')
    for index, (label, value) in enumerate(items):
        y = 48 + index * row_h
        bar_w = max(2.0, chart_w * value / axis_max)
        tooltip = f"{label}: {human_int(value)}"
        parts.append(f'<text x="0" y="{y + 20}" class="axis-label">{esc(label)}</text>')
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{left}" y="{y}" '
            f'width="{bar_w:.1f}" height="26" rx="8" fill="{color}"/>'
        )
        parts.append(f'<text x="{left + bar_w + 12:.1f}" y="{y + 19}" class="value-label">{human_int(value)}</text>')
    base_y = 47 + row_h * len(items)
    parts.append(f'<line x1="{left}" y1="46" x2="{left}" y2="{base_y}" class="axis-line"/>')
    for tick_value in axis_ticks:
        tick_x = left + chart_w * tick_value / axis_max
        parts.append(f'<line x1="{tick_x:.1f}" y1="{base_y}" x2="{tick_x:.1f}" y2="{base_y + 6}" class="axis-line"/>')
        parts.append(f'<text x="{tick_x:.1f}" y="{base_y + 24}" text-anchor="middle" class="axis-label">{human_int(tick_value)}</text>')
    parts.append(f'<line x1="{left}" y1="{base_y}" x2="{left + chart_w}" y2="{base_y}" class="axis-line"/>')
    parts.append("</svg>")
    return "".join(parts)


def line_chart(items: list[tuple[str, int]], title: str, color: str) -> str:
    if len(items) < 2:
        return '<p class="muted">Not enough data.</p>'
    width, height = 1120, 430
    left, top_pad, right, bottom = 84, 54, 32, 82
    chart_w = width - left - right
    chart_h = height - top_pad - bottom
    values = [value for _, value in items]
    min_v = 0
    axis_ticks = nice_ticks(max(values), 4)
    max_v = axis_ticks[-1] or 1

    def x(index: int) -> float:
        return left + chart_w * index / (len(items) - 1)

    def y(value: int) -> float:
        if min_v == max_v:
            return top_pad + chart_h / 2
        return top_pad + chart_h - chart_h * (value - min_v) / (max_v - min_v)

    points = " ".join(f"{x(i):.1f},{y(value):.1f}" for i, value in enumerate(values))
    area = f"{left},{top_pad + chart_h} {points} {left + chart_w},{top_pad + chart_h}"
    parts = [f'<svg class="chart" role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}">']
    parts.append(f'<text x="0" y="24" class="chart-title">{esc(title)}</text>')
    for vv in reversed(axis_ticks):
        yy = y(vv)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + chart_w}" y2="{yy:.1f}" class="grid-line"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" class="axis-label">{human_int(vv)}</text>')
    parts.append(f'<polygon points="{area}" fill="{color}" opacity=".16"/>')
    parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>')
    hover_w = chart_w / (len(items) - 1)
    for i, (label, value) in enumerate(items):
        px = x(i)
        tooltip = f"{label}: {human_int(value)}"
        x0 = max(left, px - hover_w / 2)
        x1 = min(left + chart_w, px + hover_w / 2)
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{x0:.1f}" y="{top_pad}" '
            f'width="{max(10, x1 - x0):.1f}" height="{chart_h}" fill="transparent" pointer-events="all"/>'
        )
    step = max(1, len(items) // 7)
    for i in range(0, len(items), step):
        parts.append(f'<text x="{x(i):.1f}" y="{height - 18}" text-anchor="middle" class="axis-label">{esc(items[i][0])}</text>')
    parts.append(f'<text x="{left + chart_w / 2:.1f}" y="{height - 48}" text-anchor="middle" class="axis-label">Month</text>')
    parts.append("</svg>")
    return "".join(parts)


def heatmap(matrix: dict[tuple[int, int], int], unit_label: str = "messages you wrote") -> str:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    width, height = 1120, 410
    left, top_pad = 78, 58
    cell_w, cell_h = 40, 34
    max_value = max(matrix.values()) if matrix else 1

    def heat_attrs(value: int) -> str:
        if value == 0:
            return 'fill="#101820" stroke="rgba(255,255,255,.26)" stroke-width="1"'
        opacity = 0.20 + 0.80 * (value / max_value if max_value else 0)
        return f'fill="#00AAFF" opacity="{opacity:.3f}"'

    parts = [f'<svg class="chart" role="img" aria-label="Message activity heatmap" viewBox="0 0 {width} {height}">']
    parts.append('<text x="0" y="22" class="chart-title">Your messages by weekday and hour, total count</text>')
    for hour in range(24):
        if hour % 3 == 0:
            parts.append(f'<text x="{left + hour * cell_w + cell_w / 2:.1f}" y="48" text-anchor="middle" class="axis-label">{hour:02d}</text>')
    for day_index, day_name in enumerate(days):
        y = top_pad + day_index * cell_h
        parts.append(f'<text x="65" y="{y + 21}" text-anchor="end" class="axis-label">{day_name}</text>')
        for hour in range(24):
            value = matrix.get((day_index, hour), 0)
            tooltip = f"{day_name} {hour:02d}:00, {value} {unit_label}"
            parts.append(
                f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{left + hour * cell_w}" y="{y}" '
                f'width="{cell_w - 4}" height="{cell_h - 4}" rx="7" {heat_attrs(value)}/>'
            )
    legend_y = top_pad + len(days) * cell_h + 20
    legend_values = [0]
    if max_value > 1:
        legend_values.extend([max(1, round(max_value * 0.25)), max(1, round(max_value * 0.5))])
    legend_values.append(max_value)
    deduped_legend: list[int] = []
    for value in legend_values:
        if value not in deduped_legend:
            deduped_legend.append(value)
    parts.append(f'<text x="{left}" y="{legend_y}" class="axis-label">Legend: total {esc(unit_label)} in each weekday/hour bucket; peak sets the color scale</text>')
    for index, value in enumerate(deduped_legend):
        x_pos = left + index * 170
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(f"{value} {unit_label}")} x="{x_pos}" '
            f'y="{legend_y + 13}" width="30" height="22" rx="7" {heat_attrs(value)}/>'
        )
        label = "0" if value == 0 else ("peak " + human_int(value) if value == max_value else human_int(value))
        parts.append(f'<text x="{x_pos + 39}" y="{legend_y + 30}" class="axis-label">{esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def table(headers: list[str], rows: list[list[object]], sortable: bool = False) -> str:
    table_class = ' class="sortable"' if sortable else ""
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            label = headers[index] if index < len(headers) else ""
            cells.append(f'<td data-label="{esc(label)}">{esc(cell)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table{table_class}><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def badge(label: str, value: str, note: str) -> str:
    return f'<div class="badge"><span>{esc(label)}</span><strong>{esc(value)}</strong><em>{esc(note)}</em></div>'


def keyword_tabs(
    mixed: list[tuple[str, int]],
    user: list[tuple[str, int]],
    assistant: list[tuple[str, int]],
) -> str:
    return f"""
<div class="keyword-tabs">
  <input id="kw-mixed" name="kw-view" type="radio" checked>
  <input id="kw-user" name="kw-view" type="radio">
  <input id="kw-assistant" name="kw-view" type="radio">
  <div class="tab-controls" aria-label="Keyword source">
    <label for="kw-mixed">All conversation prose</label>
    <label for="kw-user">You only</label>
    <label for="kw-assistant">ChatGPT only</label>
  </div>
  <div class="tab-panels">
    <div class="tab-panel kw-mixed-panel">{bar_chart(mixed, 'Common prose keywords in conversation text', '#00AAFF')}</div>
    <div class="tab-panel kw-user-panel">{bar_chart(user, 'Common prose keywords in your messages', '#FF755F')}</div>
    <div class="tab-panel kw-assistant-panel">{bar_chart(assistant, 'Common prose keywords in ChatGPT replies', '#FFC845')}</div>
  </div>
</div>
"""


def analyze() -> dict[str, object]:
    validation = validate_conversations_folder(EXPORT)
    conversations: list[dict[str, object]] = []
    monthly_conversations: Counter[str] = Counter()
    monthly_messages: Counter[str] = Counter()
    monthly_user_messages: Counter[str] = Counter()
    monthly_words: Counter[str] = Counter()
    daily_messages: Counter[str] = Counter()
    daily_user_messages: Counter[str] = Counter()
    weekday_hour: defaultdict[tuple[int, int], int] = defaultdict(int)
    user_weekday_hour: defaultdict[tuple[int, int], int] = defaultdict(int)
    weekday_counts: Counter[str] = Counter()
    user_weekday_counts: Counter[str] = Counter()
    hour_counts: Counter[str] = Counter()
    user_hour_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    role_words: Counter[str] = Counter()
    models: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    code_langs: Counter[str] = Counter()
    prose_counter: Counter[str] = Counter()
    user_prose_counter: Counter[str] = Counter()
    assistant_prose_counter: Counter[str] = Counter()
    title_counter: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    years: defaultdict[str, Counter[str]] = defaultdict(Counter)
    active_dates: set[date] = set()
    active_user_dates: set[date] = set()
    response_latencies: list[float] = []
    total_messages = 0
    user_messages = 0
    visible_assistant_messages = 0
    visible_messages = 0
    reasoning_messages = 0
    total_words = 0
    total_chars = 0
    user_visible_words = 0
    assistant_visible_words = 0
    reasoning_words = 0
    tool_words = 0
    system_other_words = 0
    source_bytes = 0
    latest_message_at: datetime | None = None
    earliest_message_at: datetime | None = None

    for source_file in iter_conversation_files(EXPORT):
        source_bytes += source_file.stat().st_size
        for index, conversation in enumerate(load_conversations(source_file)):
            mapping = as_string_keyed_dict(conversation.get("mapping")) or {}
            title = str(conversation.get("title") or "Untitled conversation")
            created = as_dt(conversation.get("create_time"))
            updated = as_dt(conversation.get("update_time"))
            branch_count = len(all_branch_paths(mapping, conversation_current_node(conversation)))
            models[str(conversation.get("default_model_slug") or "unknown")] += 1
            if created:
                monthly_conversations[month_key(created)] += 1
                years[str(created.year)]["conversations"] += 1
            title_counter.update(prose_words(title))
            visible_texts = [title]
            ordered_roles: list[tuple[datetime, str]] = []
            conv_messages = 0
            conv_visible = 0
            conv_reasoning = 0
            conv_words = 0
            conv_chars = 0
            conv_has_code = False
            conv_has_link = False

            for node in mapping.values():
                node_dict = as_string_keyed_dict(node)
                if node_dict is None:
                    continue
                message = as_string_keyed_dict(node_dict.get("message"))
                if message is None:
                    continue
                author = as_string_keyed_dict(message.get("author")) or {}
                role = str(author.get("role") or "unknown")
                content = as_string_keyed_dict(message.get("content")) or {}
                content_type = str(content.get("content_type") or "unknown")
                text = text_from_content(content)
                msg_words = words(text)
                prose = prose_words(text)
                word_count = count_words(text)
                created_msg = as_dt(message.get("create_time"))

                conv_messages += 1
                total_messages += 1
                conv_words += word_count
                total_words += word_count
                conv_chars += len(text)
                total_chars += len(text)
                role_counts[role] += 1
                role_words[role] += word_count
                is_reasoning = content_type in {"thoughts", "reasoning_recap"}
                if is_reasoning:
                    reasoning_messages += 1
                    conv_reasoning += 1
                    reasoning_words += word_count
                elif role in {"user", "assistant"}:
                    visible_messages += 1
                    conv_visible += 1
                    visible_texts.append(text)
                    prose_counter.update(prose)
                    if role == "user":
                        user_messages += 1
                        user_visible_words += word_count
                        user_prose_counter.update(prose)
                    else:
                        visible_assistant_messages += 1
                        assistant_visible_words += word_count
                        assistant_prose_counter.update(prose)
                elif role == "tool":
                    tool_words += word_count
                else:
                    system_other_words += word_count

                if created_msg:
                    latest_message_at = max(latest_message_at, created_msg) if latest_message_at else created_msg
                    earliest_message_at = min(earliest_message_at, created_msg) if earliest_message_at else created_msg
                    y = str(created_msg.year)
                    years[y]["messages"] += 1
                    years[y]["words"] += word_count
                    monthly_messages[month_key(created_msg)] += 1
                    monthly_words[month_key(created_msg)] += word_count
                    daily_messages[created_msg.strftime("%Y-%m-%d")] += 1
                    active_dates.add(created_msg.date())
                    weekday_counts[created_msg.strftime("%a")] += 1
                    hour_counts[f"{created_msg.hour:02d}:00"] += 1
                    weekday_hour[(created_msg.weekday(), created_msg.hour)] += 1
                    if role in {"user", "assistant"}:
                        ordered_roles.append((created_msg, role))
                    if role == "user" and not is_reasoning:
                        years[y]["user_messages"] += 1
                        monthly_user_messages[month_key(created_msg)] += 1
                        daily_user_messages[created_msg.strftime("%Y-%m-%d")] += 1
                        active_user_dates.add(created_msg.date())
                        user_weekday_counts[created_msg.strftime("%a")] += 1
                        user_hour_counts[f"{created_msg.hour:02d}:00"] += 1
                        user_weekday_hour[(created_msg.weekday(), created_msg.hour)] += 1

                for fence in FENCE_RE.finditer(text):
                    code_langs[(fence.group(2).strip().lower() or "plain")] += 1
                    conv_has_code = True
                if content_type == "code":
                    code_langs[str(content.get("language") or "plain").lower()] += 1
                    conv_has_code = True
                for url in URL_RE.findall(text):
                    domain = clean_domain(url)
                    if domain:
                        domains[domain] += 1
                        conv_has_link = True

                metadata = as_string_keyed_dict(message.get("metadata")) or {}
                refs = metadata.get("content_references")
                if isinstance(refs, list):
                    for ref in refs:
                        ref_dict = as_string_keyed_dict(ref)
                        if ref_dict is None:
                            continue
                        for url in iter_reference_urls(ref_dict):
                            domain = clean_domain(url)
                            if domain:
                                domains[domain] += 1
                                conv_has_link = True

            ordered_roles.sort(key=lambda item: item[0])
            pending_user: datetime | None = None
            for dt, role in ordered_roles:
                if role == "user":
                    pending_user = dt
                elif role == "assistant" and pending_user is not None:
                    delta = (dt - pending_user).total_seconds()
                    if 0 <= delta <= 86400:
                        response_latencies.append(delta)
                    pending_user = None

            token_set = set(prose_words("\n".join(visible_texts)))
            conv_topics: list[str] = []
            for topic, keywords in TOPICS.items():
                if token_set & keywords:
                    topics[topic] += 1
                    conv_topics.append(topic)
                    if created:
                        years[str(created.year)][f"topic:{topic}"] += 1

            conversations.append(
                {
                    "source": source_file.name,
                    "index": index,
                    "title": title,
                    "created": created,
                    "updated": updated,
                    "messages": conv_messages,
                    "visible_messages": conv_visible,
                    "reasoning_messages": conv_reasoning,
                    "words": conv_words,
                    "chars": conv_chars,
                    "branches": branch_count,
                    "duration_hours": (updated - created).total_seconds() / 3600 if created and updated else None,
                    "has_code": conv_has_code,
                    "has_link": conv_has_link,
                    "topics": conv_topics,
                }
            )

    return {
        "validation": validation,
        "conversations": conversations,
        "monthly_conversations": monthly_conversations,
        "monthly_messages": monthly_messages,
        "monthly_user_messages": monthly_user_messages,
        "monthly_words": monthly_words,
        "daily_messages": daily_messages,
        "daily_user_messages": daily_user_messages,
        "weekday_hour": weekday_hour,
        "user_weekday_hour": user_weekday_hour,
        "weekday_counts": weekday_counts,
        "user_weekday_counts": user_weekday_counts,
        "hour_counts": hour_counts,
        "user_hour_counts": user_hour_counts,
        "role_counts": role_counts,
        "role_words": role_words,
        "models": models,
        "domains": domains,
        "code_langs": code_langs,
        "prose_counter": prose_counter,
        "user_prose_counter": user_prose_counter,
        "assistant_prose_counter": assistant_prose_counter,
        "title_counter": title_counter,
        "topics": topics,
        "years": years,
        "active_dates": active_dates,
        "active_user_dates": active_user_dates,
        "response_latencies": response_latencies,
        "total_messages": total_messages,
        "user_messages": user_messages,
        "visible_assistant_messages": visible_assistant_messages,
        "visible_messages": visible_messages,
        "reasoning_messages": reasoning_messages,
        "total_words": total_words,
        "total_chars": total_chars,
        "user_visible_words": user_visible_words,
        "assistant_visible_words": assistant_visible_words,
        "reasoning_words": reasoning_words,
        "tool_words": tool_words,
        "system_other_words": system_other_words,
        "source_bytes": source_bytes,
        "earliest_message_at": earliest_message_at,
        "latest_message_at": latest_message_at,
    }


def build_html(data: dict[str, object]) -> str:
    conversations = data["conversations"]
    assert isinstance(conversations, list)
    validation = data["validation"]
    monthly_messages = data["monthly_user_messages"]
    monthly_conversations = data["monthly_conversations"]
    daily_messages = data["daily_user_messages"]
    active_dates = data["active_user_dates"]
    response_latencies = data["response_latencies"]
    assert isinstance(monthly_messages, Counter)
    assert isinstance(monthly_conversations, Counter)
    assert isinstance(daily_messages, Counter)
    assert isinstance(active_dates, set)
    assert isinstance(response_latencies, list)

    created = [c["created"] for c in conversations if isinstance(c["created"], datetime)]
    updated = [c["updated"] for c in conversations if isinstance(c["updated"], datetime)]
    message_counts = [int(c["messages"]) for c in conversations]
    word_counts = [int(c["words"]) for c in conversations]
    branch_counts = [int(c["branches"]) for c in conversations]
    total_branches = sum(branch_counts)
    earliest_message_at = data["earliest_message_at"]
    latest_message_at = data["latest_message_at"]
    first_date = earliest_message_at.strftime("%Y-%m-%d") if isinstance(earliest_message_at, datetime) else (min(created).strftime("%Y-%m-%d") if created else "unknown")
    last_date = latest_message_at.strftime("%Y-%m-%d %H:%M:%S %Z") if isinstance(latest_message_at, datetime) else (max(updated).strftime("%Y-%m-%d") if updated else "unknown")
    streak_len, streak_start, streak_end = active_streak(active_dates)
    months = sorted(set(monthly_messages) | set(monthly_conversations))
    month_rows = [(month, int(monthly_messages[month])) for month in months]
    cumulative_rows = cumulative([(month, int(monthly_conversations[month])) for month in months])
    peak_month = max(monthly_messages.items(), key=lambda item: item[1]) if monthly_messages else ("unknown", 0)
    peak_day = max(daily_messages.items(), key=lambda item: item[1]) if daily_messages else ("unknown", 0)
    peak_hour = max(data["user_hour_counts"].items(), key=lambda item: item[1]) if data["user_hour_counts"] else ("unknown", 0)
    peak_weekday = max(data["user_weekday_counts"].items(), key=lambda item: item[1]) if data["user_weekday_counts"] else ("unknown", 0)
    top_topics = top(data["topics"], 8)
    top_words = top(data["prose_counter"], 12)
    top_user_words = top(data["user_prose_counter"], 12)
    top_assistant_words = top(data["assistant_prose_counter"], 12)
    top_title_words = top(data["title_counter"], 10)
    top_domains = top(data["domains"], 14)
    top_code = top(data["code_langs"], 10)
    top_models = top(data["models"], 8)
    word_split_rows = [
        ("Your messages", int(data["user_visible_words"])),
        ("ChatGPT replies", int(data["assistant_visible_words"])),
        ("Reasoning and traces", int(data["reasoning_words"])),
        ("Tool messages", int(data["tool_words"])),
        ("System and other", int(data["system_other_words"])),
    ]
    word_split_rows = [(label, value) for label, value in word_split_rows if value > 0]
    word_split_table = [
        [label, human_int(value), f"{pct(value, int(data['total_words'])):.1f}%"]
        for label, value in word_split_rows
    ]
    biggest = sorted(conversations, key=lambda c: int(c["words"]), reverse=True)[:8]
    branchiest = sorted(conversations, key=lambda c: (int(c["branches"]), int(c["messages"])), reverse=True)[:8]
    longest = sorted(
        [c for c in conversations if isinstance(c["duration_hours"], (int, float))],
        key=lambda c: float(c["duration_hours"]),
        reverse=True,
    )[:8]
    years = data["years"]
    assert isinstance(years, defaultdict)
    year_rows: list[list[object]] = []
    for year in sorted(years):
        topic_counts = Counter({key.removeprefix("topic:"): value for key, value in years[year].items() if key.startswith("topic:")})
        year_rows.append(
            [
                year,
                human_int(years[year]["conversations"]),
                human_int(years[year]["user_messages"]),
                human_int(years[year]["messages"]),
                human_int(years[year]["words"]),
                topic_counts.most_common(1)[0][0] if topic_counts else "unknown",
            ]
        )

    badges = "".join(
        [
            badge("Conversations", human_int(len(conversations)), f"{human_int(total_branches)} total branches"),
            badge("Your messages", human_int(data["user_messages"]), f"{human_int(data['visible_assistant_messages'])} ChatGPT replies"),
            badge("Saved messages", human_int(data["total_messages"]), f"{human_int(data['reasoning_messages'])} reasoning traces kept separate"),
            badge("Active span", f"{first_date} onward", f"{human_int(len(active_dates))} days with a message from you"),
            badge("Longest daily streak", f"{human_int(streak_len)} days", f"{streak_start} to {streak_end}" if streak_start and streak_end else "no dated user messages"),
            badge("Median reply", human_duration(statistics.median(response_latencies) if response_latencies else None), f"slow replies around {human_duration(percentile(response_latencies, .9))}"),
            badge("Peak month for you", str(peak_month[0]), f"{human_int(peak_month[1])} messages you wrote"),
            badge("Peak hour for you", str(peak_hour[0]), f"{human_int(peak_hour[1])} messages you wrote"),
        ]
    )

    css = """
    :root{color-scheme:dark;--paper:#050607;--ink:#f7fbff;--muted:#a0abb4;--line:#2a343b;--panel:#10161a;--signal:#00AAFF;--hot:#FF755F;--gold:#FFC845;--wash:#16242b}
    *{box-sizing:border-box} html{scroll-snap-type:y proximity} body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif}
    body:before{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(circle at 20% -10%,rgba(0,170,255,.22),transparent 32%),radial-gradient(circle at 82% 12%,rgba(255,117,95,.15),transparent 28%),repeating-linear-gradient(90deg,rgba(255,255,255,.04) 0 1px,transparent 1px 72px),linear-gradient(180deg,#050607 0%,#0b0f11 58%,#030405 100%)} main{width:min(1520px,calc(100% - 34px));margin:0 auto;padding:26px 0 48px}
    .slide{min-height:auto;scroll-snap-align:start;display:flex;flex-direction:column;justify-content:center;padding:58px 0;border-bottom:2px solid var(--line)} .compact{min-height:auto}
    .slide:before{content:"";display:block;width:92px;height:10px;background:var(--signal);margin-bottom:24px}.eyebrow{margin:0 0 12px;color:var(--hot);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;text-transform:uppercase;font-size:16px;letter-spacing:0}.hero-title{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:clamp(38px,4.6vw,64px);font-weight:800;line-height:1;margin:0;max-width:1100px;text-transform:uppercase;letter-spacing:0}.hero-copy{font-size:23px;line-height:1.5;color:var(--muted);max-width:1040px;margin:24px 0 0}.nowrap{white-space:nowrap}
    h2{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:clamp(34px,3.45vw,54px);font-weight:800;line-height:1.05;margin:0 0 22px;text-transform:uppercase;letter-spacing:0;max-width:1220px} h3{font-size:25px;font-weight:700;margin:0 0 14px}.big-number{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:clamp(58px,7.5vw,96px);line-height:.95;margin:0;font-weight:800;color:var(--signal);letter-spacing:0}.sub{font-size:22px;color:var(--muted);line-height:1.52;max-width:960px}
    .badges{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:30px}.badge,.panel{background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.028));border:1px solid var(--line);border-radius:6px;box-shadow:8px 8px 0 rgba(0,170,255,.08),0 18px 50px rgba(0,0,0,.3)}
    .badge{padding:19px}.badge span{display:block;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:15px;font-weight:700;text-transform:uppercase;letter-spacing:0}.badge strong{display:block;margin-top:9px;font-size:28px;font-weight:700;line-height:1.12}.badge em{display:block;margin-top:9px;color:var(--muted);font-style:normal;font-size:16px;line-height:1.4}
    .grid{display:grid;gap:26px;margin-top:30px}.two,.three{grid-template-columns:minmax(0,1fr)}.panel{padding:30px;overflow:visible}.statline{display:flex;justify-content:space-between;gap:16px;border-top:1px solid rgba(255,255,255,.12);padding:14px 0;color:var(--muted)}.statline strong{color:var(--ink)}
    .chart{width:100%;height:auto;min-width:0;display:block}.chart-title{fill:#f7fbff;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-weight:700;font-size:27px}.axis-label{fill:#abb6be;font-size:17px}.value-label{fill:#f7fbff;font-size:17px;font-weight:700}.grid-line{stroke:rgba(255,255,255,.14);stroke-width:1}.axis-line{stroke:rgba(255,255,255,.36);stroke-width:1}.hover-target{cursor:help;outline:none;pointer-events:all}.hover-target:focus{filter:brightness(1.25)}
    .chart-tooltip{position:fixed;left:0;top:0;z-index:50;max-width:min(320px,calc(100vw - 28px));padding:10px 12px;border:1px solid rgba(0,170,255,.55);border-radius:6px;background:rgba(5,8,10,.96);color:#f7fbff;box-shadow:0 18px 48px rgba(0,0,0,.42);font-size:15px;font-weight:700;line-height:1.35;opacity:0;pointer-events:none;transform:translate(-9999px,-9999px);transition:opacity .08s ease}.chart-tooltip.is-visible{opacity:1}
    table{width:100%;border-collapse:collapse;font-size:18px;line-height:1.44}th{text-align:left;color:#dce9ef;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:15px;font-weight:700;text-transform:uppercase;border-bottom:2px solid var(--signal);padding:14px 10px;letter-spacing:0;white-space:normal}td{border-bottom:1px solid rgba(255,255,255,.1);padding:15px 10px;vertical-align:top;color:#f7fbff;overflow-wrap:anywhere}.sortable th{cursor:pointer}.sortable th[data-direction=asc]:after{content:" ↑";color:#8ea0aa}.sortable th[data-direction=desc]:after{content:" ↓";color:#8ea0aa}.muted{color:var(--muted)}
    .keyword-tabs>input{position:absolute;opacity:0;pointer-events:none}.tab-controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}.tab-controls label{border:1px solid var(--line);border-radius:999px;padding:10px 13px;background:var(--wash);font-size:15px;font-weight:700;cursor:pointer}.tab-panel{display:none}#kw-mixed:checked~.tab-controls label[for=kw-mixed],#kw-user:checked~.tab-controls label[for=kw-user],#kw-assistant:checked~.tab-controls label[for=kw-assistant]{background:var(--signal);color:#021014;border-color:var(--signal)}#kw-mixed:checked~.tab-panels .kw-mixed-panel,#kw-user:checked~.tab-panels .kw-user-panel,#kw-assistant:checked~.tab-panels .kw-assistant-panel{display:block}
    @media(max-width:980px){.badges,.two,.three{grid-template-columns:1fr}.panel{box-shadow:5px 5px 0 rgba(0,170,255,.08)}}
    @media(max-width:700px){main{width:calc(100% - 24px);padding-top:18px}.slide{padding:42px 0}.hero-copy,.sub{font-size:20px}.big-number{font-size:clamp(50px,16vw,64px)}h2{font-size:clamp(29px,8vw,36px)}.panel{padding:18px;overflow:hidden}table,tbody,tr,td{display:block;width:100%}thead{display:none}tbody tr{border-bottom:1px solid rgba(255,255,255,.12);padding:10px 0}tbody tr:last-child{border-bottom:0}td{border-bottom:0;padding:8px 0;display:grid;grid-template-columns:minmax(94px,36%) minmax(0,1fr);gap:12px}td:before{content:attr(data-label);color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0}.statline{display:block}.statline strong{display:block;margin-top:4px}}
    """
    sort_script = """
document.querySelectorAll("table.sortable").forEach((table) => {
  table.querySelectorAll("th").forEach((th, column) => {
    th.addEventListener("click", () => {
      const tbody = table.tBodies[0];
      const direction = th.dataset.direction === "asc" ? "desc" : "asc";
      table.querySelectorAll("th").forEach((header) => { header.dataset.direction = ""; });
      th.dataset.direction = direction;
      const rows = Array.from(tbody.rows);
      const valueFor = (row) => row.cells[column]?.textContent?.trim() || "";
      const numeric = (value) => Number(value.replace(/[^0-9.-]/g, ""));
      rows.sort((left, right) => {
        const a = valueFor(left);
        const b = valueFor(right);
        const an = numeric(a);
        const bn = numeric(b);
        const result = Number.isFinite(an) && Number.isFinite(bn) && a.match(/[0-9]/) && b.match(/[0-9]/)
          ? an - bn
          : a.localeCompare(b, undefined, {numeric: true, sensitivity: "base"});
        return direction === "asc" ? result : -result;
      });
      rows.forEach((row) => tbody.appendChild(row));
    });
  });
});

const chartTooltip = document.createElement("div");
chartTooltip.className = "chart-tooltip";
chartTooltip.setAttribute("role", "tooltip");
document.body.appendChild(chartTooltip);

const moveChartTooltip = (clientX, clientY) => {
  const margin = 14;
  const offset = 16;
  const rect = chartTooltip.getBoundingClientRect();
  let left = clientX + offset;
  let top = clientY + offset;
  if (left + rect.width + margin > window.innerWidth) {
    left = clientX - rect.width - offset;
  }
  if (top + rect.height + margin > window.innerHeight) {
    top = clientY - rect.height - offset;
  }
  chartTooltip.style.transform = "translate(" + Math.max(margin, left) + "px," + Math.max(margin, top) + "px)";
};

const showChartTooltip = (target, clientX, clientY) => {
  const text = target.dataset.tooltip;
  if (!text) return;
  chartTooltip.textContent = text;
  chartTooltip.classList.add("is-visible");
  moveChartTooltip(clientX, clientY);
};

const hideChartTooltip = () => {
  chartTooltip.classList.remove("is-visible");
};

document.querySelectorAll("[data-tooltip]").forEach((target) => {
  target.addEventListener("mouseenter", (event) => showChartTooltip(target, event.clientX, event.clientY));
  target.addEventListener("mousemove", (event) => moveChartTooltip(event.clientX, event.clientY));
  target.addEventListener("mouseleave", hideChartTooltip);
  target.addEventListener("focus", () => {
    const rect = target.getBoundingClientRect();
    showChartTooltip(target, rect.left + rect.width / 2, rect.top + rect.height / 2);
  });
  target.addEventListener("blur", hideChartTooltip);
});
"""

    weekday_rows = [(day, int(data["user_weekday_counts"][day])) for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
    shape_rows = [
        ["Median messages per conversation", human_int(statistics.median(message_counts))],
        ["Messages per conversation, high end", human_int(percentile(message_counts, .9))],
        ["Messages per conversation, very high end", human_int(percentile(message_counts, .99))],
        ["Median words per conversation", human_int(statistics.median(word_counts))],
        ["Words per conversation, high end", human_int(percentile(word_counts, .9))],
        ["Words per conversation, very high end", human_int(percentile(word_counts, .99))],
        ["Conversations including code", f"{pct(sum(1 for c in conversations if c['has_code']), len(conversations)):.1f}%"],
        ["Conversations with links", f"{pct(sum(1 for c in conversations if c['has_link']), len(conversations)):.1f}%"],
    ]
    shape_definition_rows = [
        [
            "Conversations with links",
            "Conversations where at least one URL or ChatGPT citation/reference URL was detected anywhere in the conversation.",
        ],
        [
            "Conversations including code",
            "Conversations where at least one code block or message saved as code was detected. This is not test coverage.",
        ],
    ]
    peak_rows = [
        ["Busiest month", peak_month[0], human_int(peak_month[1])],
        ["Busiest day", peak_day[0], human_int(peak_day[1])],
        ["Busiest weekday", peak_weekday[0], human_int(peak_weekday[1])],
        ["Busiest hour", peak_hour[0], human_int(peak_hour[1])],
    ]
    peak_definition_rows = [
        [
            "Messages you wrote",
            "Peak moments in this section count only timestamped messages authored by you.",
        ],
    ]
    biggest_messages = sorted(conversations, key=lambda c: int(c["messages"]), reverse=True)[:8]
    biggest_rows = [[c["title"], human_int(c["words"]), human_int(c["messages"]), human_int(c["branches"])] for c in biggest]
    biggest_message_rows = [[c["title"], human_int(c["messages"]), human_int(c["words"]), human_int(c["branches"])] for c in biggest_messages]
    branch_rows = [[c["title"], human_int(c["branches"]), human_int(c["messages"]), human_int(c["words"])] for c in branchiest]
    duration_rows = [
        [
            c["title"],
            human_duration(float(c["duration_hours"]) * 3600),
            human_int(c["messages"]),
            human_int(c["words"]),
        ]
        for c in longest
    ]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Time ChatGPT Wrapped</title><style>{css}</style></head><body><main>
<section class="slide"><p class="eyebrow">All-Time ChatGPT Wrapped</p><h1 class="hero-title">ChatGPT history, all time</h1><p class="hero-copy">A full-history summary of your ChatGPT conversations from {esc(first_date)} onward. Export as of <span class="nowrap">{esc(last_date)}</span>.</p><div class="badges">{badges}</div></section>
<section class="slide"><p class="eyebrow">The headline number</p><p class="big-number">{human_int(data['user_messages'])}</p><h2>messages you wrote</h2><p class="sub">The export also contains {human_int(data['visible_assistant_messages'])} ChatGPT replies and {human_int(data['reasoning_messages'])} reasoning traces. Reasoning traces are kept separate from your activity peaks.</p></section>
<section class="slide"><p class="eyebrow">Word accounting</p><h2>The words are split by source.</h2><p class="sub">Word totals cover all saved text. Prose keyword charts use conversation body text and filter out reasoning, URLs, code blocks, and common code tokens.</p><div class="grid two"><div class="panel">{bar_chart(word_split_rows, 'Words by source', '#00AAFF')}</div><div class="panel">{table(['Source','Words','Share'], word_split_table)}</div></div></section>
<section class="slide"><p class="eyebrow">Activity over time</p><h2>Messages by month</h2><div class="grid two"><div class="panel">{line_chart(month_rows, 'Your messages by month', '#00AAFF')}</div><div class="panel">{line_chart(cumulative_rows, 'Cumulative conversations', '#FF755F')}</div></div></section>
<section class="slide"><p class="eyebrow">Busiest periods</p><h2>Peak activity based on your messages</h2><p class="sub">These peaks count only timestamped messages authored by you. The 2026-03-16 spike across all saved messages is mostly reasoning traces, not thousands of messages you wrote.</p><div class="grid two"><div class="panel">{table(['Moment','When','Messages you wrote'], peak_rows)}</div><div class="panel">{bar_chart(weekday_rows, 'Your messages by weekday', '#FFC845')}</div></div><div class="panel" style="margin-top:18px">{table(['Metric','Meaning'], peak_definition_rows)}</div></section>
<section class="slide"><p class="eyebrow">Local time</p><h2>Your message heatmap</h2><p class="sub">Each square is the total number of messages you wrote in that weekday and hour across the whole export. It is not an average, median, or maximum; brighter cells are closer to the busiest weekday/hour bucket.</p><div class="panel">{heatmap(data['user_weekday_hour'])}</div></section>
<section class="slide"><p class="eyebrow">Conversation size</p><h2>Conversation size</h2><div class="panel">{table(['Metric','Value'], shape_rows)}</div><div class="panel" style="margin-top:18px">{table(['Metric','Meaning'], shape_definition_rows)}</div></section>
<section class="slide"><p class="eyebrow">Topics</p><h2>Recurring topics</h2><div class="grid two"><div class="panel">{bar_chart(top_topics, 'Conversation topics', '#00AAFF')}</div><div class="panel">{keyword_tabs(top_words, top_user_words, top_assistant_words)}</div></div></section>
<section class="slide"><p class="eyebrow">Titles and models</p><h2>Conversation titles and models</h2><div class="grid two"><div class="panel">{bar_chart(top_title_words, 'Title keywords', '#00AAFF')}</div><div class="panel">{bar_chart(top_models, 'Most used models by conversation', '#FFC845')}</div></div></section>
<section class="slide"><p class="eyebrow">Code and links</p><h2>Code blocks and linked sites</h2><p class="sub">Linked/reference sites are websites found in message links, ChatGPT citations, and embedded source references.</p><div class="grid two"><div class="panel">{bar_chart(top_code, 'Code block languages', '#00AAFF')}</div><div class="panel">{bar_chart(top_domains, 'Linked/reference sites', '#FF755F')}</div></div></section>
<section class="slide"><p class="eyebrow">Leaderboards</p><h2>Largest conversations</h2><div class="grid three"><div class="panel"><h3>Largest by words</h3>{table(['Title','Words','Saved messages','Branches'], biggest_rows, sortable=True)}</div><div class="panel"><h3>Largest by messages</h3>{table(['Title','Saved messages','Words','Branches'], biggest_message_rows, sortable=True)}</div><div class="panel"><h3>Most branched</h3>{table(['Title','Branches','Saved messages','Words'], branch_rows, sortable=True)}</div></div></section>
<section class="slide"><p class="eyebrow">Longest conversations</p><h2>Conversations with the longest time span</h2><div class="panel">{table(['Title','Duration','Saved messages','Words'], duration_rows, sortable=True)}</div></section>
<section class="slide compact"><p class="eyebrow">Year by year</p><h2>All-time breakdown</h2><div class="panel">{table(['Year','Conversations','Your messages','Saved messages','Words','Top topic'], year_rows, sortable=True)}</div></section>
<section class="slide compact"><div class="panel"><h3>Conversion check</h3><p class="muted">Export as of {esc(last_date)}. Checked {human_int(validation.conversations_scanned)} conversations and {human_int(validation.messages_scanned)} saved messages.</p></div></section>
</main><script>{sort_script}</script></body></html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an all-time ChatGPT Wrapped HTML report."
    )
    parser.add_argument(
        "conversations_folder",
        type=Path,
        help="Path to the Conversations folder from a ChatGPT data export.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("output/chatgpt-wrapped-all-time"),
        help="Directory for the generated HTML and JSON files.",
    )
    parser.add_argument(
        "--timezone",
        default="Europe/Zurich",
        help="IANA timezone used for local activity charts. Default: Europe/Zurich.",
    )
    return parser.parse_args()


def configure_runtime(
    conversations_folder: Path, output_dir: Path, timezone_name: str
) -> None:
    global EXPORT, OUT_DIR, HTML_PATH, STATS_PATH, TZ

    try:
        TZ = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Unknown timezone: {timezone_name}") from exc

    EXPORT = conversations_folder.expanduser().resolve()
    OUT_DIR = output_dir.expanduser().resolve()
    HTML_PATH = OUT_DIR / "chatgpt-wrapped-all-time.html"
    STATS_PATH = OUT_DIR / "chatgpt-wrapped-all-time-stats.json"


def main() -> None:
    args = parse_args()
    configure_runtime(args.conversations_folder, args.output_dir, args.timezone)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = analyze()
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    conversations = data["conversations"]
    summary = {
        "generated_at": datetime.now(TZ).isoformat(),
        "html_path": str(HTML_PATH),
        "source_files": data["validation"].files_scanned,
        "conversations": len(conversations),
        "messages": data["total_messages"],
        "user_messages": data["user_messages"],
        "visible_assistant_messages": data["visible_assistant_messages"],
        "visible_messages": data["visible_messages"],
        "reasoning_messages": data["reasoning_messages"],
        "branches": sum(int(c["branches"]) for c in conversations),
        "words": data["total_words"],
        "word_split": {
            "user_visible_words": data["user_visible_words"],
            "assistant_visible_words": data["assistant_visible_words"],
            "reasoning_words": data["reasoning_words"],
            "tool_words": data["tool_words"],
            "system_other_words": data["system_other_words"],
        },
        "active_user_days": len(data["active_user_dates"]),
        "latest_message_at": data["latest_message_at"].isoformat() if data["latest_message_at"] else None,
        "top_topics": data["topics"].most_common(8),
        "top_code_languages": data["code_langs"].most_common(8),
        "top_link_reference_domains": data["domains"].most_common(8),
        "top_user_prose_words": data["user_prose_counter"].most_common(14),
        "top_assistant_prose_words": data["assistant_prose_counter"].most_common(14),
    }
    STATS_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
