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
OUT_DIR = Path("output/conversation-report")
HTML_PATH = OUT_DIR / "chatgpt-conversation-report.html"
STATS_PATH = OUT_DIR / "chatgpt-conversation-report-stats.json"
TZ = ZoneInfo("Europe/Zurich")

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{1,}")
COUNT_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#._-]*")
FENCE_RE = re.compile(r"^\s*([`~]{3,})([A-Za-z0-9_+.#-]*)", re.MULTILINE)
FENCED_BLOCK_RE = re.compile(r"(^|\n)\s*([`~]{3,}).*?\2", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
HTML_ENTITY_RE = re.compile(r"&[A-Za-z0-9#]+;")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")

STOPWORDS = {
    "about", "after", "again", "all", "also", "and", "any", "are", "around",
    "ask", "asked", "because", "been", "before", "being", "best", "better",
    "both", "but", "can", "chat", "chatgpt", "could", "create", "current",
    "did", "does", "doing", "done", "each", "else", "etc", "even", "every",
    "example", "examples", "few", "file", "files", "find", "first", "for",
    "from", "get", "getting", "give", "good", "had", "has", "have", "help",
    "here", "how", "into", "issue", "its", "just", "latest", "like", "look",
    "looking", "make", "many", "may", "more", "most", "much", "need", "needs",
    "new", "not", "now", "off", "old", "one", "only", "other", "out", "over",
    "please", "possible", "problem", "really", "right", "same", "second",
    "should", "some", "something", "sure", "tell", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "thing", "things",
    "third", "this", "those", "through", "today", "tomorrow", "use", "used",
    "using", "want", "was", "way", "were", "what", "when", "where", "which",
    "while", "with", "without", "work", "working", "works", "would", "write",
    "yesterday", "you", "your", "youre",
}

PROSE_TOKEN_BLOCKLIST = {
    "__init__", "argc", "argv", "array", "async", "await", "boolean", "bool",
    "class", "classname", "const", "constructor", "def", "dict", "div", "elif",
    "enum", "false", "float", "function", "html", "https", "impl", "import",
    "int", "interface", "json", "key", "keys", "lambda", "let", "localhost",
    "nbsp", "none", "null", "object", "param", "path", "private", "props",
    "public", "return", "self", "span", "src", "static", "string", "struct",
    "text", "throws", "true", "type", "value", "values", "var", "void", "x00",
}

TOPICS = {
    "Software engineering": {
        "api", "backend", "bug", "ci", "code", "css", "database", "dependency",
        "docker", "frontend", "function", "git", "github", "html", "java",
        "javascript", "json", "linux", "next", "node", "package", "podman",
        "postgres", "python", "react", "repo", "repository", "script", "sql",
        "test", "tests", "typescript", "yaml",
    },
    "AI and agents": {
        "agent", "agents", "ai", "assistant", "claude", "codex", "embedding",
        "gpt", "llm", "model", "models", "openai", "prompt", "prompts",
        "reasoning", "tool",
    },
    "DevOps and infrastructure": {
        "aws", "azure", "cloud", "container", "containers", "deploy",
        "deployment", "dns", "docker", "flux", "gcp", "helm", "k8s",
        "kubernetes", "linux", "network", "nginx", "podman", "proxy", "server",
        "ssh", "terraform",
    },
    "Hardware and devices": {
        "avr", "battery", "camera", "device", "devices", "disk", "display",
        "drive", "iphone", "keyboard", "laptop", "lenovo", "mac", "monitor",
        "nas", "phone", "projector", "router", "ssd", "usb",
    },
    "Design and UX": {
        "accessibility", "button", "color", "colors", "component", "contrast",
        "design", "figma", "font", "layout", "shadcn", "style", "tailwind",
        "theme", "ui", "ux",
    },
    "Writing and docs": {
        "article", "copy", "document", "draft", "email", "markdown", "message",
        "post", "readme", "rewrite", "summary", "summarize", "tone", "wording",
    },
    "Legal and licensing": {
        "agpl", "compliance", "contract", "copyright", "gpl", "legal", "license",
        "licensing", "mit", "policy", "privacy", "terms",
    },
    "Shopping and products": {
        "buy", "camera", "clothes", "coat", "compare", "dress", "price",
        "product", "recommend", "recommendation", "review", "reviews", "shop",
        "shopping",
    },
    "Travel and places": {
        "airport", "basel", "city", "country", "flight", "hotel", "map",
        "restaurant", "route", "switzerland", "train", "travel", "trip",
        "zurich",
    },
    "Personal admin": {
        "account", "appointment", "bank", "budget", "calendar", "insurance",
        "invoice", "plan", "planning", "schedule", "tax", "time",
    },
    "Health and fitness": {
        "diet", "doctor", "exercise", "fitness", "health", "medical",
        "medicine", "pain", "sleep", "symptom",
    },
    "Entertainment and culture": {
        "book", "celebrity", "film", "game", "games", "halloween", "movie",
        "music", "series", "show", "song",
    },
}


def esc(value):
    return html.escape(str(value), quote=True)


def tooltip_attr(value):
    return f'data-tooltip="{esc(value)}" tabindex="0"'


def as_dt(value):
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


def month_key(dt):
    return dt.strftime("%Y-%m") if dt else "unknown"


def words(text):
    result = []
    for match in WORD_RE.finditer(text.lower()):
        word = match.group(0).strip("._-")
        if len(word) >= 3 and word not in STOPWORDS and not word.isdigit():
            result.append(word)
    return result


def count_words(text):
    return sum(1 for _ in COUNT_WORD_RE.finditer(text))


def prose_words(text):
    without_code = FENCED_BLOCK_RE.sub("\n", text)
    without_inline_code = INLINE_CODE_RE.sub(" ", without_code)
    without_entities = HTML_ENTITY_RE.sub(" ", without_inline_code)
    without_urls = URL_RE.sub(" ", without_entities)
    return [word for word in words(without_urls) if word not in PROSE_TOKEN_BLOCKLIST]


def content_body_strings(content):
    content_type = str(content.get("content_type") or "")
    parts = content.get("parts")
    if content_type in {"text", "multimodal_text"} and isinstance(parts, list):
        strings = []
        for part in parts:
            if isinstance(part, str):
                strings.append(part)
            else:
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


def text_from_content(content):
    return remove_known_private_use_marker_forms("\n".join(content_body_strings(content)))


def clean_domain(url):
    try:
        cleaned = clean_url(url.rstrip(".,;:"))
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    return parsed.netloc.lower().removeprefix("www.")


def iter_reference_urls(reference):
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


def human_int(value):
    return f"{int(round(value)):,}"


def nice_ticks(max_value, target_steps=4):
    if max_value <= 0:
        return [0, 1]
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
    ticks = []
    current = 0.0
    while current <= axis_max + step / 2:
        ticks.append(current)
        current += step
    return ticks


def human_bytes(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024
    return f"{size:,.1f} GiB"


def human_duration(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def percentile(values, p):
    if not values:
        return 0
    values = sorted(float(v) for v in values)
    idx = (len(values) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - idx) + values[hi] * (idx - lo)


def pct(part, total):
    return 0 if not total else 100 * part / total


def top(counter, n=12):
    return [(str(label), int(value)) for label, value in counter.most_common(n)]


def top_by_year(counters_by_year, n=12):
    return {year: top(counter, n) for year, counter in sorted(counters_by_year.items())}


def active_streak(dates):
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


def cumulative(items):
    total = 0
    rows = []
    for label, value in items:
        total += value
        rows.append((label, total))
    return rows


def bar_chart(
    items,
    title,
    color="#00AAFF",
    width=980,
    row_h=32,
    label_width=None,
    x_axis_label="Count",
    y_axis_label="",
):
    if not items:
        return '<p class="empty">No data.</p>'
    max_value = max(float(value) for _, value in items) or 1
    axis_ticks = nice_ticks(max_value, 2)
    axis_max = axis_ticks[-1] or 1
    if label_width is None:
        label_width = min(260, max(110, max(len(str(label)) for label, _ in items) * 7))
    left = label_width
    right = 155
    chart_w = width - left - right
    height = 128 + row_h * len(items)
    parts = [f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" class="chart">']
    parts.append(f'<text x="0" y="20" class="chart-title">{esc(title)}</text>')
    if y_axis_label:
        parts.append(f'<text x="0" y="38" class="axis-note">{esc(y_axis_label)}</text>')
    for i, (label, value) in enumerate(items):
        y = 46 + i * row_h
        bar_w = max(1, chart_w * float(value) / axis_max)
        tooltip = f"{label}: {human_int(value)}"
        parts.append(f'<text x="0" y="{y + 15}" class="axis-label">{esc(label)}</text>')
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{left}" y="{y}" '
            f'width="{bar_w:.1f}" height="19" rx="3" fill="{color}"/>'
        )
        parts.append(f'<text x="{left + bar_w + 8:.1f}" y="{y + 14}" class="value-label">{human_int(value)}</text>')
    base_y = 45 + row_h * len(items)
    parts.append(f'<line x1="{left}" y1="44" x2="{left}" y2="{base_y}" class="axis-line"/>')
    for tick_value in axis_ticks:
        tick_x = left + chart_w * tick_value / axis_max
        parts.append(f'<line x1="{tick_x:.1f}" y1="{base_y}" x2="{tick_x:.1f}" y2="{base_y + 5}" class="axis-line"/>')
        parts.append(f'<text x="{tick_x:.1f}" y="{base_y + 23}" text-anchor="middle" class="axis-label">{human_int(tick_value)}</text>')
    parts.append(f'<line x1="{left}" y1="{base_y}" x2="{left + chart_w}" y2="{base_y}" class="axis-line"/>')
    parts.append(f'<text x="{left + chart_w / 2:.1f}" y="{base_y + 54}" text-anchor="middle" class="axis-note">{esc(x_axis_label)}</text>')
    parts.append("</svg>")
    mobile_rows = []
    for label, value in items:
        percent = 100 * float(value) / max_value
        mobile_rows.append(
            '<div class="mobile-bar-row">'
            f'<div class="mobile-bar-head"><span>{esc(label)}</span><strong>{human_int(value)}</strong></div>'
            f'<span class="mobile-bar-track"><span style="width:{percent:.2f}%;background:{color}"></span></span>'
            "</div>"
        )
    return (
        '<div class="chart-desktop">'
        + "".join(parts)
        + '</div><div class="mobile-bars">'
        + f"<h3>{esc(title)}</h3>"
        + "".join(mobile_rows)
        + "</div>"
    )


def line_chart(items, title, color="#00AAFF", y_axis_label="Count"):
    if len(items) < 2:
        return '<p class="empty">Not enough data.</p>'
    width, height = 1060, 380
    left, top_pad, right, bottom = 80, 50, 28, 74
    chart_w = width - left - right
    chart_h = height - top_pad - bottom
    values = [float(v) for _, v in items]
    min_v = 0
    axis_ticks = nice_ticks(max(values), 4)
    max_v = axis_ticks[-1] or 1
    def x(i):
        return left + chart_w * i / (len(items) - 1)
    def y(value):
        if min_v == max_v:
            return top_pad + chart_h / 2
        return top_pad + chart_h - chart_h * (value - min_v) / (max_v - min_v)
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    area = f"{left},{top_pad + chart_h} {points} {left + chart_w},{top_pad + chart_h}"
    parts = [f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" class="chart">']
    parts.append(f'<text x="0" y="20" class="chart-title">{esc(title)}</text>')
    for vv in reversed(axis_ticks):
        yy = y(vv)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + chart_w}" y2="{yy:.1f}" class="grid-line"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" class="axis-label">{human_int(vv)}</text>')
    parts.append(f'<polygon points="{area}" fill="{color}" opacity=".12"/>')
    parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>')
    hover_w = chart_w / (len(items) - 1)
    for i, (label, value) in enumerate(items):
        px = x(i)
        tooltip = f"{label}: {human_int(value)}"
        x0 = max(left, px - hover_w / 2)
        x1 = min(left + chart_w, px + hover_w / 2)
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{x0:.1f}" y="{top_pad}" '
            f'width="{max(8, x1 - x0):.1f}" height="{chart_h}" fill="transparent" pointer-events="all"/>'
        )
    step = max(1, len(items) // 8)
    for i in range(0, len(items), step):
        parts.append(f'<text x="{x(i):.1f}" y="{height - 42}" text-anchor="middle" class="axis-label">{esc(items[i][0])}</text>')
    parts.append(f'<text x="{left + chart_w / 2:.1f}" y="{height - 14}" text-anchor="middle" class="axis-note">Month</text>')
    parts.append(f'<text x="18" y="{top_pad + chart_h / 2:.1f}" transform="rotate(-90 18 {top_pad + chart_h / 2:.1f})" text-anchor="middle" class="axis-note">{esc(y_axis_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def heatmap(matrix, title, unit_label="your messages"):
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    max_value = max(matrix.values()) if matrix else 1

    def heat_attrs(value):
        if value == 0:
            return 'fill="#182024" stroke="#33424a" stroke-width="1"'
        opacity = 0.18 + 0.82 * (value / max_value if max_value else 0)
        return f'fill="#00AAFF" opacity="{opacity:.3f}"'

    def svg(width, height, left, top_pad, cell_w, cell_h, radius, hour_step, legend_gap, legend_values, heading):
        parts = [f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" class="chart">']
        parts.append(f'<text x="0" y="20" class="chart-title">{esc(heading)}</text>')
        for hour in range(24):
            if hour % hour_step == 0:
                parts.append(f'<text x="{left + hour * cell_w + cell_w/2:.1f}" y="{top_pad - 10}" text-anchor="middle" class="axis-label">{hour:02d}</text>')
        for day_idx, day in enumerate(days):
            y = top_pad + day_idx * cell_h
            parts.append(f'<text x="{left - 13}" y="{y + cell_h * .62:.1f}" text-anchor="end" class="axis-label">{day}</text>')
            for hour in range(24):
                value = matrix.get((day_idx, hour), 0)
                tooltip = f"{day} {hour:02d}:00 - {value} {unit_label}"
                parts.append(
                    f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{left + hour * cell_w}" y="{y}" '
                    f'width="{cell_w - 4}" height="{cell_h - 4}" rx="{radius}" {heat_attrs(value)}/>'
                )
        legend_y = top_pad + len(days) * cell_h + 18
        parts.append(f'<text x="{left}" y="{legend_y}" class="axis-label">Total {esc(unit_label)} per weekday/hour; peak sets color</text>')
        for index, value in enumerate(legend_values):
            x_pos = left + index * legend_gap
            parts.append(
                f'<rect class="hover-target" {tooltip_attr(f"{value} {unit_label}")} x="{x_pos}" '
                f'y="{legend_y + 12}" width="{max(18, cell_w - 10)}" height="{max(14, cell_h - 12)}" rx="{radius}" {heat_attrs(value)}/>'
            )
            label = "0" if value == 0 else ("peak " + human_int(value) if value == max_value else human_int(value))
            parts.append(f'<text x="{x_pos + max(26, cell_w)}" y="{legend_y + 27}" class="axis-label">{esc(label)}</text>')
        parts.append("</svg>")
        return "".join(parts)

    desktop_legend = [0]
    if max_value > 1:
        desktop_legend.extend([max(1, round(max_value * 0.25)), max(1, round(max_value * 0.5))])
    desktop_legend.append(max_value)
    desktop_legend = list(dict.fromkeys(desktop_legend))

    mobile_legend = [0]
    if max_value > 1:
        mobile_legend.append(max(1, round(max_value * 0.5)))
    mobile_legend.append(max_value)
    mobile_legend = list(dict.fromkeys(mobile_legend))

    return (
        '<div class="heatmap-desktop">'
        + svg(1060, 390, 72, 72, 39, 30, 3, 3, 145, desktop_legend, title)
        + '</div><div class="heatmap-mobile">'
        + svg(560, 265, 44, 47, 20, 22, 4, 6, 125, mobile_legend, "Messages by weekday/hour, totals")
        + "</div>"
    )


def scatter_plot(points, title, x_label, y_label, color="#2563eb"):
    if not points:
        return '<p class="empty">No data.</p>'
    width, height = 860, 420
    left, top_pad, right, bottom = 70, 44, 28, 58
    chart_w = width - left - right
    chart_h = height - top_pad - bottom
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    max_x = max(xs) or 1
    max_y = max(ys) or 1

    def x(value):
        return left + chart_w * math.log1p(float(value)) / math.log1p(max_x)

    def y(value):
        return top_pad + chart_h - chart_h * math.log1p(float(value)) / math.log1p(max_y)

    parts = [f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" class="chart">']
    parts.append(f'<text x="0" y="18" class="chart-title">{esc(title)}</text>')
    for tick in range(5):
        yy = top_pad + chart_h * tick / 4
        xx = left + chart_w * tick / 4
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + chart_w}" y2="{yy:.1f}" class="grid-line"/>')
        parts.append(f'<line x1="{xx:.1f}" y1="{top_pad}" x2="{xx:.1f}" y2="{top_pad + chart_h}" class="grid-line"/>')
    for x_value, y_value, label in points:
        radius = 2.4 if y_value < 10 else 3.6
        parts.append(
            f'<circle class="hover-target" {tooltip_attr(f"{label}: {human_int(x_value)} {x_label}, {human_int(y_value)} {y_label}")} '
            f'cx="{x(x_value):.1f}" cy="{y(y_value):.1f}" r="{radius}" fill="{color}" opacity=".42"/>'
        )
    parts.append(f'<text x="{left + chart_w / 2:.1f}" y="{height - 14}" text-anchor="middle" class="axis-label">{esc(x_label)} log scale</text>')
    parts.append(f'<text x="14" y="{top_pad + chart_h / 2:.1f}" transform="rotate(-90 14 {top_pad + chart_h / 2:.1f})" text-anchor="middle" class="axis-label">{esc(y_label)} log scale</text>')
    parts.append("</svg>")
    return "".join(parts)


def format_bin_edge(value):
    if isinstance(value, float) and not value.is_integer():
        return f"{value:g}"
    return human_int(value)


def histogram(values, bins, title, color="#00AAFF", x_axis_label="Conversations", y_axis_label="Range"):
    rows = []
    for low, high in zip(bins, bins[1:]):
        count = sum(1 for value in values if low <= value < high)
        label = f"{format_bin_edge(low)}+" if high > 999_999_999 else f"{format_bin_edge(low)}-{format_bin_edge(high)}"
        rows.append((label, count))
    return bar_chart(rows, title, color=color, row_h=27, label_width=130, x_axis_label=x_axis_label, y_axis_label=y_axis_label)


def table(headers, rows, sortable=False):
    table_class = ' class="sortable"' if sortable else ""
    sort_hint = " sortable" if sortable else ""
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            label = headers[index] if index < len(headers) else ""
            cells.append(f'<td data-label="{esc(label)}">{esc(cell)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap{sort_hint}"><table{table_class}><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def year_chart_panel(chart_id, title, all_items, yearly_items, years, color, label_width=220, x_axis_label="Count"):
    parts = [f'<div class="panel year-panel" data-year-panel="{esc(chart_id)}">']
    parts.append(f'<div class="year-chart" data-year-chart="all">{bar_chart(all_items, f"{title}, all years", color, label_width=label_width, x_axis_label=x_axis_label)}</div>')
    for year in years:
        items = yearly_items.get(year, [])
        parts.append(f'<div class="year-chart" data-year-chart="{esc(year)}" hidden>{bar_chart(items, f"{title}, {year}", color, label_width=label_width, x_axis_label=x_axis_label)}</div>')
    parts.append("</div>")
    return "".join(parts)


def year_explorer(years, panels):
    buttons = ['<button type="button" data-year-value="all" aria-pressed="true">All</button>']
    buttons.extend(f'<button type="button" data-year-value="{esc(year)}" aria-pressed="false">{esc(year)}</button>' for year in years)
    return f"""
<div class="year-explorer" data-year-explorer>
  <div class="year-controls" aria-label="Choose year">{"".join(buttons)}</div>
  <div class="grid two">{"".join(panels)}</div>
</div>
"""


def kpi(label, value, note=""):
    return f'<div class="kpi"><span>{esc(label)}</span><strong>{esc(value)}</strong><em>{esc(note)}</em></div>'


def signal_strip(items, title):
    if not items:
        return ""
    width, height = 1060, 138
    left, right, top_pad, bottom = 56, 24, 34, 28
    chart_w = width - left - right
    chart_h = height - top_pad - bottom
    max_value = max(value for _, value in items) or 1
    step = chart_w / len(items)
    parts = [f'<svg role="img" aria-label="{esc(title)}" viewBox="0 0 {width} {height}" class="signal-strip">']
    parts.append(f'<text x="0" y="18" class="chart-title">{esc(title)}</text>')
    for index, (label, value) in enumerate(items):
        x = left + index * step
        bar_h = max(1, chart_h * value / max_value)
        y = top_pad + chart_h - bar_h
        tooltip = f"{label}: {human_int(value)} messages you wrote"
        parts.append(
            f'<rect class="hover-target" {tooltip_attr(tooltip)} x="{x:.1f}" y="{y:.1f}" '
            f'width="{max(1, step - 2):.1f}" height="{bar_h:.1f}" rx="2" fill="#00AAFF"/>'
        )
    for index in range(0, len(items), max(1, len(items) // 6)):
        x = left + index * step
        parts.append(f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" class="axis-label">{esc(items[index][0])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def keyword_tabs(mixed, user, assistant):
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
    <div class="tab-panel kw-mixed-panel">{bar_chart(mixed, 'Common prose keywords in conversation text', '#00AAFF', label_width=210, x_axis_label='Occurrences')}</div>
    <div class="tab-panel kw-user-panel">{bar_chart(user, 'Common prose keywords in your messages', '#FF755F', label_width=210, x_axis_label='Occurrences')}</div>
    <div class="tab-panel kw-assistant-panel">{bar_chart(assistant, 'Common prose keywords in ChatGPT replies', '#A7B1BD', label_width=210, x_axis_label='Occurrences')}</div>
  </div>
</div>
"""


def analyze():
    validation = validate_conversations_folder(EXPORT)
    conversations = []
    role_counts = Counter()
    role_words = Counter()
    statuses = Counter()
    channels = Counter()
    models = Counter()
    ref_types = Counter()
    domains = Counter()
    attachments = Counter()
    code_langs = Counter()
    title_words = Counter()
    message_words = Counter()
    prose_message_words = Counter()
    user_prose_message_words = Counter()
    assistant_prose_message_words = Counter()
    topics = Counter()
    yearly_topics = defaultdict(Counter)
    yearly_models = defaultdict(Counter)
    yearly_domains = defaultdict(Counter)
    yearly_code_langs = defaultdict(Counter)
    yearly_title_words = defaultdict(Counter)
    yearly_prose_message_words = defaultdict(Counter)
    yearly_user_prose_message_words = defaultdict(Counter)
    yearly_assistant_prose_message_words = defaultdict(Counter)
    monthly_conversations = Counter()
    monthly_messages = Counter()
    monthly_user_messages = Counter()
    monthly_words = Counter()
    daily_messages = Counter()
    daily_user_messages = Counter()
    hour_counts = Counter()
    user_hour_counts = Counter()
    weekday_counts = Counter()
    user_weekday_counts = Counter()
    weekday_hour = defaultdict(int)
    user_weekday_hour = defaultdict(int)
    response_latencies = []
    message_total = 0
    reasoning_total = 0
    user_message_total = 0
    assistant_message_total = 0
    total_words = 0
    total_chars = 0
    user_message_words = 0
    assistant_reply_words = 0
    reasoning_words = 0
    tool_words = 0
    system_other_words = 0
    active_user_dates = set()
    latest_message_at = None
    earliest_message_at = None

    for source_file in iter_conversation_files(EXPORT):
        for index, conversation in enumerate(load_conversations(source_file)):
            mapping = as_string_keyed_dict(conversation.get("mapping")) or {}
            title = str(conversation.get("title") or "Untitled conversation")
            created = as_dt(conversation.get("create_time"))
            updated = as_dt(conversation.get("update_time"))
            branch_count = len(all_branch_paths(mapping, conversation_current_node(conversation)))
            conversation_year = str(created.year) if created else None
            model_name = str(conversation.get("default_model_slug") or "unknown")
            models[model_name] += 1
            if conversation_year:
                yearly_models[conversation_year][model_name] += 1
            if created:
                monthly_conversations[month_key(created)] += 1
            title_words.update(words(title))
            if conversation_year:
                yearly_title_words[conversation_year].update(words(title))
            transcript_text = [title]
            conv_has_link = False
            conv_has_code = False
            conv_words = conv_chars = user_words = assistant_words = 0
            conv_messages = user_messages = assistant_messages = tool_messages = reasoning_messages = 0
            ordered_roles = []

            for node_id, node in mapping.items():
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
                char_count = len(text)
                created_msg = as_dt(message.get("create_time"))
                message_year = str(created_msg.year) if created_msg else conversation_year
                conv_words += word_count
                conv_chars += char_count
                conv_messages += 1
                message_total += 1
                total_words += word_count
                total_chars += char_count
                role_counts[role] += 1
                role_words[role] += word_count
                statuses[str(message.get("status") or "unknown")] += 1
                channels[str(message.get("channel") or "unknown")] += 1
                if role == "user":
                    user_messages += 1
                    user_words += word_count
                    user_message_total += 1
                elif role == "assistant":
                    assistant_messages += 1
                    assistant_words += word_count
                    assistant_message_total += 1
                elif role == "tool":
                    tool_messages += 1
                is_reasoning = content_type in {"thoughts", "reasoning_recap"}
                if is_reasoning:
                    reasoning_messages += 1
                    reasoning_total += 1
                    reasoning_words += word_count
                elif role in {"user", "assistant"}:
                    transcript_text.append(text)
                    message_words.update(msg_words)
                    prose_message_words.update(prose)
                    if message_year:
                        yearly_prose_message_words[message_year].update(prose)
                    if role == "user":
                        user_message_words += word_count
                        user_prose_message_words.update(prose)
                        if message_year:
                            yearly_user_prose_message_words[message_year].update(prose)
                    else:
                        assistant_reply_words += word_count
                        assistant_prose_message_words.update(prose)
                        if message_year:
                            yearly_assistant_prose_message_words[message_year].update(prose)
                elif role == "tool":
                    tool_words += word_count
                else:
                    system_other_words += word_count
                if created_msg:
                    latest_message_at = max(latest_message_at, created_msg) if latest_message_at else created_msg
                    earliest_message_at = min(earliest_message_at, created_msg) if earliest_message_at else created_msg
                    monthly_messages[month_key(created_msg)] += 1
                    monthly_words[month_key(created_msg)] += word_count
                    daily_messages[created_msg.strftime("%Y-%m-%d")] += 1
                    hour_counts[f"{created_msg.hour:02d}:00"] += 1
                    weekday_counts[created_msg.strftime("%a")] += 1
                    weekday_hour[(created_msg.weekday(), created_msg.hour)] += 1
                    if role in {"user", "assistant"}:
                        ordered_roles.append((created_msg, role))
                    if role == "user" and not is_reasoning:
                        monthly_user_messages[month_key(created_msg)] += 1
                        daily_user_messages[created_msg.strftime("%Y-%m-%d")] += 1
                        active_user_dates.add(created_msg.date())
                        user_hour_counts[f"{created_msg.hour:02d}:00"] += 1
                        user_weekday_counts[created_msg.strftime("%a")] += 1
                        user_weekday_hour[(created_msg.weekday(), created_msg.hour)] += 1

                for fence in FENCE_RE.finditer(text):
                    code_language = fence.group(2).strip().lower() or "plain"
                    code_langs[code_language] += 1
                    if message_year:
                        yearly_code_langs[message_year][code_language] += 1
                    conv_has_code = True
                if content_type == "code":
                    code_language = str(content.get("language") or "plain").lower()
                    code_langs[code_language] += 1
                    if message_year:
                        yearly_code_langs[message_year][code_language] += 1
                    conv_has_code = True
                for url in URL_RE.findall(text):
                    domain = clean_domain(url)
                    if domain:
                        domains[domain] += 1
                        if message_year:
                            yearly_domains[message_year][domain] += 1
                        conv_has_link = True
                parts = content.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        part_dict = as_string_keyed_dict(part)
                        if part_dict is None:
                            continue
                        if isinstance(part_dict.get("asset_pointer"), str):
                            attachments["asset_pointer"] += 1
                        if isinstance(part_dict.get("file_id"), str):
                            attachments["file_id"] += 1
                metadata = as_string_keyed_dict(message.get("metadata")) or {}
                references = metadata.get("content_references")
                if isinstance(references, list):
                    for reference in references:
                        reference_dict = as_string_keyed_dict(reference)
                        if reference_dict is None:
                            continue
                        ref_types[str(reference_dict.get("type") or "unspecified")] += 1
                        for url in iter_reference_urls(reference_dict):
                            domain = clean_domain(url)
                            if domain:
                                domains[domain] += 1
                                if message_year:
                                    yearly_domains[message_year][domain] += 1
                                conv_has_link = True

            ordered_roles.sort(key=lambda item: item[0])
            pending_user = None
            for dt, role in ordered_roles:
                if role == "user":
                    pending_user = dt
                elif role == "assistant" and pending_user is not None:
                    delta = (dt - pending_user).total_seconds()
                    if 0 <= delta <= 86400:
                        response_latencies.append(delta)
                    pending_user = None

            token_set = set(prose_words("\n".join(transcript_text).lower()))
            conv_topics = []
            for topic, keywords in TOPICS.items():
                if token_set & keywords:
                    topics[topic] += 1
                    if conversation_year:
                        yearly_topics[conversation_year][topic] += 1
                    conv_topics.append(topic)

            conversations.append({
                "source_file": source_file.name,
                "index": index,
                "title": title,
                "created": created,
                "updated": updated,
                "duration": (updated - created).total_seconds() if created and updated else None,
                "nodes": len(mapping),
                "messages": conv_messages,
                "user_messages": user_messages,
                "assistant_messages": assistant_messages,
                "tool_messages": tool_messages,
                "reasoning_messages": reasoning_messages,
                "branches": branch_count,
                "words": conv_words,
                "chars": conv_chars,
                "user_words": user_words,
                "assistant_words": assistant_words,
                "archived": conversation.get("is_archived") is True,
                "starred": conversation.get("is_starred") is True,
                "do_not_remember": conversation.get("is_do_not_remember") is True,
                "has_links": conv_has_link,
                "has_code": conv_has_code,
                "topics": conv_topics,
            })

    return {
        "validation": validation,
        "conversations": conversations,
        "message_total": message_total,
        "reasoning_total": reasoning_total,
        "user_message_total": user_message_total,
        "assistant_message_total": assistant_message_total,
        "total_words": total_words,
        "total_chars": total_chars,
        "user_message_words": user_message_words,
        "assistant_reply_words": assistant_reply_words,
        "reasoning_words": reasoning_words,
        "tool_words": tool_words,
        "system_other_words": system_other_words,
        "role_counts": role_counts,
        "role_words": role_words,
        "statuses": statuses,
        "channels": channels,
        "models": models,
        "ref_types": ref_types,
        "domains": domains,
        "attachments": attachments,
        "code_langs": code_langs,
        "title_words": title_words,
        "message_words": message_words,
        "prose_message_words": prose_message_words,
        "user_prose_message_words": user_prose_message_words,
        "assistant_prose_message_words": assistant_prose_message_words,
        "topics": topics,
        "yearly_topics": yearly_topics,
        "yearly_models": yearly_models,
        "yearly_domains": yearly_domains,
        "yearly_code_langs": yearly_code_langs,
        "yearly_title_words": yearly_title_words,
        "yearly_prose_message_words": yearly_prose_message_words,
        "yearly_user_prose_message_words": yearly_user_prose_message_words,
        "yearly_assistant_prose_message_words": yearly_assistant_prose_message_words,
        "monthly_conversations": monthly_conversations,
        "monthly_messages": monthly_messages,
        "monthly_user_messages": monthly_user_messages,
        "monthly_words": monthly_words,
        "daily_messages": daily_messages,
        "daily_user_messages": daily_user_messages,
        "hour_counts": hour_counts,
        "user_hour_counts": user_hour_counts,
        "weekday_counts": weekday_counts,
        "user_weekday_counts": user_weekday_counts,
        "weekday_hour": weekday_hour,
        "user_weekday_hour": user_weekday_hour,
        "response_latencies": response_latencies,
        "active_user_dates": active_user_dates,
        "earliest_message_at": earliest_message_at,
        "latest_message_at": latest_message_at,
    }


def build_html(data):
    conversations = data["conversations"]
    validation = data["validation"]
    branches = sum(c["branches"] for c in conversations)
    message_counts = [c["messages"] for c in conversations]
    word_counts = [c["words"] for c in conversations]
    branch_counts = [c["branches"] for c in conversations]
    durations = [c["duration"] / 3600 for c in conversations if c["duration"] is not None and c["duration"] >= 0]
    created = [c["created"] for c in conversations if c["created"]]
    months = sorted(set(data["monthly_conversations"]) | set(data["monthly_user_messages"]))
    monthly_conversations = [(month, data["monthly_conversations"][month]) for month in months]
    monthly_user_messages = [(month, data["monthly_user_messages"][month]) for month in months]
    cumulative_conversations = cumulative(monthly_conversations)
    active_user_days = len(data["active_user_dates"])
    streak_len, streak_start, streak_end = active_streak(data["active_user_dates"])
    top_user_days = data["daily_user_messages"].most_common(12)
    peak_user_day = max(data["daily_user_messages"].items(), key=lambda item: item[1]) if data["daily_user_messages"] else ("unknown", 0)
    peak_recorded_day = max(data["daily_messages"].items(), key=lambda item: item[1]) if data["daily_messages"] else ("unknown", 0)
    latest_message_at = data["latest_message_at"]
    latest_label = latest_message_at.strftime("%Y-%m-%d %H:%M:%S %Z") if latest_message_at else "unknown"
    first_label = min(created).strftime("%Y-%m-%d") if created else "unknown"
    assistant_reply_messages = sum(c["assistant_messages"] - c["reasoning_messages"] for c in conversations)
    word_split_rows = [
        ("Your messages", int(data["user_message_words"])),
        ("ChatGPT replies", int(data["assistant_reply_words"])),
        ("Reasoning and traces", int(data["reasoning_words"])),
        ("Tool messages", int(data["tool_words"])),
        ("System and other", int(data["system_other_words"])),
    ]
    word_split_rows = [(label, value) for label, value in word_split_rows if value > 0]
    word_split_table = [
        [label, human_int(value), f"{pct(value, data['total_words']):.1f}%"]
        for label, value in word_split_rows
        if value
    ]
    cards = "".join([
        kpi("Conversations", human_int(len(conversations)), f"{human_int(branches)} total branch versions"),
        kpi("Your messages", human_int(data["user_message_total"]), f"{human_int(active_user_days)} days with at least one message from you"),
        kpi("ChatGPT replies", human_int(assistant_reply_messages), f"{human_int(data['reasoning_total'])} reasoning traces"),
        kpi("Most messages in one day", f"{human_int(peak_user_day[1])} messages", f"you wrote on {peak_user_day[0]}"),
        kpi("Longest daily streak", f"{human_int(streak_len)} days", f"{streak_start} to {streak_end}" if streak_start and streak_end else "no dated user messages"),
        kpi("All saved words", human_int(data["total_words"]), "split by source below"),
        kpi("Assistant response time", f"{human_duration(percentile(data['response_latencies'], .5))} / {human_duration(percentile(data['response_latencies'], .9))} / {human_duration(percentile(data['response_latencies'], .99))}", f"median / 90th / 99th percentile across {human_int(len(data['response_latencies']))} replies"),
        kpi("Conversations including code", f"{pct(sum(c['has_code'] for c in conversations), len(conversations)):.1f}%", f"{human_int(sum(c['has_code'] for c in conversations))} with detected code blocks"),
    ])
    biggest_words = sorted(conversations, key=lambda c: c["words"], reverse=True)[:12]
    biggest_messages = sorted(conversations, key=lambda c: c["messages"], reverse=True)[:12]
    branchy = sorted(conversations, key=lambda c: (c["branches"], c["messages"]), reverse=True)[:12]
    stats_rows = [
        ["Conversations checked", human_int(validation.conversations_scanned)],
        ["Messages checked", human_int(validation.messages_scanned)],
        ["Messages per conversation, typical / high / very high", f"{percentile(message_counts, .5):.0f} / {percentile(message_counts, .9):.0f} / {percentile(message_counts, .99):.0f}"],
        ["Words per conversation, typical / high / very high", f"{percentile(word_counts, .5):.0f} / {percentile(word_counts, .9):.0f} / {percentile(word_counts, .99):.0f}"],
        ["Assistant response time, typical / high / very high", f"{human_duration(percentile(data['response_latencies'], .5))} / {human_duration(percentile(data['response_latencies'], .9))} / {human_duration(percentile(data['response_latencies'], .99))}"],
        ["2026-03-16 check", f"{human_int(data['daily_user_messages'].get('2026-03-16', 0))} messages from you; {human_int(data['daily_messages'].get('2026-03-16', 0))} saved messages, mostly reasoning traces."],
    ]
    weekday_rows = [(day, data["user_weekday_counts"][day]) for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
    year_options = sorted(
        set(data["yearly_topics"])
        | set(data["yearly_models"])
        | set(data["yearly_domains"])
        | set(data["yearly_code_langs"])
        | set(data["yearly_title_words"])
        | set(data["yearly_user_prose_message_words"])
    )
    yearly_section = year_explorer(
        year_options,
        [
            year_chart_panel(
                "topics",
                "Conversation topics",
                top(data["topics"], 12),
                top_by_year(data["yearly_topics"], 12),
                year_options,
                "#00AAFF",
                label_width=220,
                x_axis_label="Conversations",
            ),
            year_chart_panel(
                "models",
                "Most used models by conversation",
                top(data["models"], 12),
                top_by_year(data["yearly_models"], 12),
                year_options,
                "#A7B1BD",
                label_width=210,
                x_axis_label="Conversations",
            ),
            year_chart_panel(
                "sites",
                "Linked/reference sites",
                top(data["domains"], 12),
                top_by_year(data["yearly_domains"], 12),
                year_options,
                "#FFC845",
                label_width=220,
                x_axis_label="Links and citations",
            ),
            year_chart_panel(
                "code",
                "Code block languages",
                top(data["code_langs"], 12),
                top_by_year(data["yearly_code_langs"], 12),
                year_options,
                "#A7B1BD",
                label_width=130,
                x_axis_label="Code blocks",
            ),
            year_chart_panel(
                "title-keywords",
                "Title keywords",
                top(data["title_words"], 12),
                top_by_year(data["yearly_title_words"], 12),
                year_options,
                "#FF755F",
                label_width=170,
                x_axis_label="Uses",
            ),
            year_chart_panel(
                "your-keywords",
                "Common words in your messages",
                top(data["user_prose_message_words"], 12),
                top_by_year(data["yearly_user_prose_message_words"], 12),
                year_options,
                "#00AAFF",
                label_width=210,
                x_axis_label="Uses",
            ),
        ],
    )
    css = """
    :root{color-scheme:dark;--paper:#090b0c;--ink:#f4f7f8;--muted:#9aa7ae;--line:#29343a;--panel:#111619;--signal:#00AAFF;--oxide:#FF755F;--citrus:#FFC845;--graphite:#A7B1BD;--mist:#1a252a}
    *{box-sizing:border-box} body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif}
    body:before{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(circle at 12% 0%,rgba(0,170,255,.16),transparent 32%),radial-gradient(circle at 85% 8%,rgba(255,117,95,.12),transparent 30%),repeating-linear-gradient(90deg,rgba(255,255,255,.035) 0 1px,transparent 1px 78px),linear-gradient(180deg,#07090a 0%,#0b1012 56%,#050607 100%)}
    main{width:min(1540px,calc(100vw - 36px));margin:0 auto;padding:32px 0 56px}.hero{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(360px,.8fr);gap:28px;align-items:end;border-bottom:2px solid var(--signal);padding:18px 0 28px;margin-bottom:18px}
    .eyebrow{margin:0 0 10px;color:var(--oxide);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;text-transform:uppercase;font-size:15px;letter-spacing:0}h1{margin:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:clamp(36px,4.25vw,60px);font-weight:800;line-height:1.04;letter-spacing:0;text-transform:uppercase;max-width:900px}h2{margin:0 0 12px;font-size:23px;font-weight:700;line-height:1.2}.lede{margin:18px 0 0;color:var(--muted);font-size:20px;line-height:1.58;max-width:860px}
    .hero-card{border-left:2px solid var(--line);padding-left:20px}.hero-card strong{display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--signal);font-size:19px;margin-bottom:8px}.hero-card p{margin:0;color:var(--muted);font-size:16px;line-height:1.5}
    .kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.kpi,.panel{background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.025));border:1px solid var(--line);box-shadow:0 14px 34px rgba(0,0,0,.32)}
    .kpi{border-radius:6px;padding:18px}.kpi span{display:block;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:0}.kpi strong{display:block;margin-top:9px;font-size:26px;font-weight:700;line-height:1.15}.kpi em{display:block;margin-top:9px;color:var(--muted);font-style:normal;font-size:15px;line-height:1.4}
    .grid{display:grid;gap:24px;margin-top:20px;align-items:start}.two,.three{grid-template-columns:minmax(0,1fr)}.panel{border-radius:6px;padding:26px}.wide{margin-top:20px}.caption{margin:0 0 16px;color:var(--muted);font-size:17px;line-height:1.55;max-width:1040px}.section-label{margin:38px 0 14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--oxide);font-weight:700;text-transform:uppercase;font-size:15px;letter-spacing:0}
    .year-controls{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}.year-controls button{appearance:none;border:1px solid var(--line);border-radius:999px;background:var(--mist);color:var(--ink);padding:9px 13px;font-size:15px;font-weight:700;cursor:pointer}.year-controls button[aria-pressed=true]{background:var(--signal);border-color:var(--signal);color:#031014}.year-chart[hidden]{display:none}
    .chart,.signal-strip{width:100%;height:auto;min-width:0;display:block}.chart-title{fill:#f4f7f8;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-weight:700;font-size:22px}.axis-label{fill:#a9b5bc;font-size:15px}.axis-note{fill:#a9b5bc;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px}.value-label{fill:#f2f8fb;font-size:15px;font-weight:700}.grid-line{stroke:rgba(255,255,255,.105);stroke-width:1}.axis-line{stroke:rgba(255,255,255,.32);stroke-width:1}.hover-target{cursor:help;outline:none;pointer-events:all}.hover-target:focus{filter:brightness(1.25)}.heatmap-mobile,.mobile-bars{display:none}.mobile-bars h3{font-size:22px;margin:0 0 12px}.mobile-bar-row{padding:10px 0;border-bottom:1px solid rgba(255,255,255,.1)}.mobile-bar-row:last-child{border-bottom:0}.mobile-bar-head{display:flex;justify-content:space-between;gap:14px;align-items:baseline;color:#f4f7f8;font-size:15px;line-height:1.3}.mobile-bar-head span{overflow-wrap:anywhere}.mobile-bar-head strong{font-size:15px;white-space:nowrap}.mobile-bar-track{display:block;height:8px;margin-top:7px;border-radius:999px;background:rgba(255,255,255,.10);overflow:hidden}.mobile-bar-track span{display:block;height:100%;border-radius:999px}
    .chart-tooltip{position:fixed;left:0;top:0;z-index:50;max-width:min(320px,calc(100vw - 28px));padding:10px 12px;border:1px solid rgba(0,170,255,.55);border-radius:6px;background:rgba(5,8,10,.96);color:#f4f7f8;box-shadow:0 18px 48px rgba(0,0,0,.42);font-size:15px;font-weight:700;line-height:1.35;opacity:0;pointer-events:none;transform:translate(-9999px,-9999px);transition:opacity .08s ease}.chart-tooltip.is-open{opacity:1}
    table{width:100%;border-collapse:collapse;font-size:16px;line-height:1.45}th{text-align:left;color:#d9e5ea;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:0;border-bottom:1px solid var(--signal);padding:12px 10px;white-space:normal}td{border-bottom:1px solid rgba(255,255,255,.08);padding:13px 10px;vertical-align:top;overflow-wrap:anywhere}tr:hover td{background:rgba(0,170,255,.07)}.table-wrap{width:100%;max-width:100%}.sortable th{cursor:pointer}.sortable th[data-direction=asc]:after{content:" ↑";color:#7f8f96}.sortable th[data-direction=desc]:after{content:" ↓";color:#7f8f96}.empty{color:var(--muted)}
    .keyword-tabs>input{position:absolute;opacity:0;pointer-events:none}.tab-controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.tab-controls label{border:1px solid var(--line);border-radius:999px;padding:9px 12px;background:var(--mist);font-size:15px;font-weight:700;cursor:pointer}.tab-panel{display:none}#kw-mixed:checked~.tab-controls label[for=kw-mixed],#kw-user:checked~.tab-controls label[for=kw-user],#kw-assistant:checked~.tab-controls label[for=kw-assistant]{background:var(--ink);color:var(--paper);border-color:var(--ink)}#kw-mixed:checked~.tab-panels .kw-mixed-panel,#kw-user:checked~.tab-panels .kw-user-panel,#kw-assistant:checked~.tab-panels .kw-assistant-panel{display:block}
    footer{color:var(--muted);font-size:15px;margin-top:30px;padding-top:18px;border-top:2px solid var(--ink)}@media(max-width:1100px){.hero,.two,.three,.kpis{grid-template-columns:1fr}main{width:min(100vw - 24px,940px)}.hero-card{border-left:0;border-top:2px solid var(--ink);padding:16px 0 0}}
    @media(max-width:700px){main{width:calc(100% - 24px);padding-top:22px}.lede{font-size:19px}.hero-card strong{font-size:17px}.signal-strip{display:none}.panel{padding:18px;overflow:hidden}.heatmap-desktop,.chart-desktop{display:none}.heatmap-mobile,.mobile-bars{display:block}table,tbody,tr,td{display:block;width:100%}thead{display:none}tbody tr{border-bottom:1px solid rgba(255,255,255,.12);padding:10px 0}tbody tr:last-child{border-bottom:0}td{border-bottom:0;padding:8px 0;display:grid;grid-template-columns:minmax(98px,38%) minmax(0,1fr);gap:12px}td:before{content:attr(data-label);color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0}}
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

document.querySelectorAll("[data-year-explorer]").forEach((explorer) => {
  const buttons = Array.from(explorer.querySelectorAll("[data-year-value]"));
  const charts = Array.from(explorer.querySelectorAll("[data-year-chart]"));
  const setYear = (year) => {
    buttons.forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.yearValue === year ? "true" : "false");
    });
    charts.forEach((chart) => {
      chart.hidden = chart.dataset.yearChart !== year;
    });
  };
  buttons.forEach((button) => {
    button.addEventListener("click", () => setYear(button.dataset.yearValue || "all"));
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
  chartTooltip.classList.add("is-open");
  moveChartTooltip(clientX, clientY);
};

const hideChartTooltip = () => {
  chartTooltip.classList.remove("is-open");
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
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChatGPT Conversation Field Report</title><style>{css}</style></head><body><main>
<section class="hero"><div><p class="eyebrow">ChatGPT Conversation Field Report</p><h1>Conversation history report</h1></div><div class="hero-card"><strong>Export as of {esc(latest_label)}</strong><p>Conversation range starts {esc(first_label)}. Charts use totals.</p></div></section>
{signal_strip(monthly_user_messages, 'Messages you wrote by month')}
<section class="kpis">{cards}</section>
<p class="section-label">Words by source</p>
<section class="grid two"><div class="panel"><p class="caption">Word totals cover your messages, ChatGPT replies, and reasoning traces as separate sources.</p>{bar_chart(word_split_rows, 'Words by source', '#00AAFF', label_width=230, x_axis_label='Words')}</div><div class="panel"><h2>Source totals</h2>{table(['Source','Words','Share'], word_split_table)}</div></section>
<p class="section-label">Activity</p>
<p class="caption">Message activity charts in this section count timestamped messages authored by you. The conversation-created chart uses conversation start dates.</p>
<section class="grid two"><div class="panel">{line_chart(monthly_user_messages, 'Your messages by month', '#00AAFF', 'Messages you wrote')}</div><div class="panel">{line_chart(monthly_conversations, 'Conversations created by month', '#FF755F', 'Conversations')}</div></section>
<section class="grid two"><div class="panel">{line_chart(cumulative_conversations, 'Cumulative conversation count', '#A7B1BD', 'Conversations')}</div><div class="panel">{bar_chart(weekday_rows, 'Your messages by weekday', '#FFC845', label_width=90, x_axis_label='Messages you wrote')}</div></section>
<p class="section-label">Local time</p>
<section class="panel wide"><p class="caption">Each square is the total number of messages you wrote in that weekday and hour across the whole export, converted to Europe/Zurich. Brighter cells are closer to the busiest weekday/hour bucket.</p>{heatmap(data['user_weekday_hour'], 'Your message activity by weekday and hour, Europe/Zurich', 'messages you wrote')}</section>
<p class="section-label">Conversation size and branches</p>
<section class="grid two"><div class="panel"><p class="caption">Saved messages are the unique messages preserved in each conversation, including your messages, ChatGPT replies, reasoning traces, and other saved entries.</p>{histogram(message_counts, [1,2,4,8,16,32,64,128,256,512,1_000_000_000], 'How many messages conversations contain', '#00AAFF', 'Conversations', 'Saved messages')}</div><div class="panel"><p class="caption">Alternate branches are complete conversation paths. Shared messages are counted once, even when they appear in more than one branch.</p>{histogram(branch_counts, [1,2,3,4,5,8,13,21,34,55,1_000_000_000], 'Alternate branches per conversation', '#FF755F', 'Conversations', 'Branches')}</div></section>
<section class="grid two"><div class="panel">{histogram(durations, [0,.5,1,6,24,72,168,720,2160,8760,1_000_000_000], 'How long conversations lasted', '#A7B1BD', 'Conversations', 'Hours from first to last update')}</div><div class="panel">{histogram(data['response_latencies'], [0,2,5,10,30,60,120,300,600,1800,3600,86400], 'How long ChatGPT took to reply', '#FFC845', 'Replies', 'Seconds')}</div></section>
<p class="section-label">Language and topics</p>
<p class="caption">Topic and keyword charts use conversation text. Prose keyword charts use natural-language message text; code block languages and linked sites have their own charts.</p>
<section class="grid two"><div class="panel">{bar_chart(top(data['topics'], 16), 'Conversation topics', '#00AAFF', label_width=220, x_axis_label='Conversations')}</div><div class="panel">{keyword_tabs(top(data['prose_message_words'], 12), top(data['user_prose_message_words'], 12), top(data['assistant_prose_message_words'], 12))}</div></section>
<section class="grid three"><div class="panel">{bar_chart(top(data['title_words'], 18), 'Title keywords', '#FF755F', label_width=170, x_axis_label='Uses')}</div><div class="panel"><p class="caption">Code languages come from detected code blocks and messages saved as code.</p>{bar_chart(top(data['code_langs'], 12), 'Code block languages', '#A7B1BD', label_width=130, x_axis_label='Code blocks')}</div><div class="panel"><p class="caption">Linked/reference sites are websites found in message links, ChatGPT citations, and embedded source references.</p>{bar_chart(top(data['domains'], 12), 'Linked/reference sites', '#FFC845', label_width=220, x_axis_label='Links and citations')}</div></section>
<p class="section-label">Year-by-year comparisons</p>
<p class="caption">Use the year buttons to apply the same time slice to every chart in this section.</p>
{yearly_section}
<section class="panel wide"><h2>Busiest Days for Your Messages</h2><p class="caption">This table uses timestamped messages authored by you.</p>{table(['Date','Messages you wrote'], [[day, human_int(count)] for day, count in top_user_days], sortable=True)}</section>
<section class="grid two"><div class="panel"><h2>Largest conversations by word count</h2><p class="caption">Saved messages are counted once per conversation, even when the same message appears in multiple branches.</p>{table(['Title','Updated','Saved messages','Branches','Words'], [[c['title'], c['updated'].strftime('%Y-%m-%d') if c['updated'] else 'unknown', human_int(c['messages']), human_int(c['branches']), human_int(c['words'])] for c in biggest_words], sortable=True)}</div><div class="panel"><h2>Largest conversations by message count</h2><p class="caption">Words include all saved text in the conversation, with reasoning traces included in this size ranking.</p>{table(['Title','Updated','Saved messages','Branches','Words'], [[c['title'], c['updated'].strftime('%Y-%m-%d') if c['updated'] else 'unknown', human_int(c['messages']), human_int(c['branches']), human_int(c['words'])] for c in biggest_messages], sortable=True)}</div></section>
<section class="grid two"><div class="panel"><h2>Conversations with the most branches</h2><p class="caption">Branches are complete alternate conversation paths.</p>{table(['Title','Saved messages','Branches','Words'], [[c['title'], human_int(c['messages']), human_int(c['branches']), human_int(c['words'])] for c in branchy], sortable=True)}</div><div class="panel"><h2>Distribution and conversion checks</h2>{table(['Metric','Value'], stats_rows)}</div></section>
<footer>Built locally from a ChatGPT data export. Export as of {esc(latest_label)}.</footer>
</main><script>{sort_script}</script></body></html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a full ChatGPT conversation history field report."
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
        default=Path("output/conversation-report"),
        help="Directory for the generated HTML and JSON files.",
    )
    parser.add_argument(
        "--timezone",
        default="Europe/Zurich",
        help="IANA timezone used for local activity charts. Default: Europe/Zurich.",
    )
    return parser.parse_args()


def configure_runtime(conversations_folder, output_dir, timezone_name):
    global EXPORT, OUT_DIR, HTML_PATH, STATS_PATH, TZ

    try:
        TZ = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Unknown timezone: {timezone_name}") from exc

    EXPORT = conversations_folder.expanduser().resolve()
    OUT_DIR = output_dir.expanduser().resolve()
    HTML_PATH = OUT_DIR / "chatgpt-conversation-report.html"
    STATS_PATH = OUT_DIR / "chatgpt-conversation-report-stats.json"


def main():
    args = parse_args()
    configure_runtime(args.conversations_folder, args.output_dir, args.timezone)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = analyze()
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    summary = {
        "generated_at": datetime.now(TZ).isoformat(),
        "html_path": str(HTML_PATH),
        "source_files": data["validation"].files_scanned,
        "conversations": len(data["conversations"]),
        "messages": data["message_total"],
        "branches": sum(c["branches"] for c in data["conversations"]),
        "words": data["total_words"],
        "word_split": {
            "user_message_words": data["user_message_words"],
            "assistant_reply_words": data["assistant_reply_words"],
            "reasoning_words": data["reasoning_words"],
            "tool_words": data["tool_words"],
            "system_other_words": data["system_other_words"],
        },
        "active_user_days": len(data["active_user_dates"]),
        "latest_message_at": data["latest_message_at"].isoformat() if data["latest_message_at"] else None,
        "link_conversations": sum(c["has_links"] for c in data["conversations"]),
        "code_conversations": sum(c["has_code"] for c in data["conversations"]),
        "top_topics": data["topics"].most_common(10),
        "top_domains": data["domains"].most_common(10),
        "top_title_words": data["title_words"].most_common(20),
        "top_prose_words": data["prose_message_words"].most_common(20),
        "top_user_prose_words": data["user_prose_message_words"].most_common(20),
        "top_assistant_prose_words": data["assistant_prose_message_words"].most_common(20),
        "by_year": {
            "topics": top_by_year(data["yearly_topics"], 20),
            "models": top_by_year(data["yearly_models"], 20),
            "linked_reference_sites": top_by_year(data["yearly_domains"], 20),
            "code_block_languages": top_by_year(data["yearly_code_langs"], 20),
            "title_keywords": top_by_year(data["yearly_title_words"], 20),
            "prose_keywords_all_conversation_text": top_by_year(data["yearly_prose_message_words"], 20),
            "prose_keywords_your_messages": top_by_year(data["yearly_user_prose_message_words"], 20),
            "prose_keywords_chatgpt_replies": top_by_year(data["yearly_assistant_prose_message_words"], 20),
        },
    }
    STATS_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
