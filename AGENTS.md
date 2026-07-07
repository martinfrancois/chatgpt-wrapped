# Agent Notes

This repository contains reusable scripts for generating local, private HTML reports from a ChatGPT data export.

- Do not commit real ChatGPT export data, generated reports, generated stats JSON, screenshots from private reports, or extracted transcript content.
- Keep the generators clone-friendly: input paths, output paths, and timezone must be CLI options rather than hard-coded local paths.
- Use synthetic fixtures for tests. Do not use snippets from private conversations.
- Keep report wording human-friendly and avoid export-internal labels unless they are clearly explained.
- Do not use one global "how to read this report" explanation block. Put metric explanations next to the specific chart, table, or KPI where the reader needs them.
- Preserve hover tooltips, readable chart text, dark-mode-first styling, and year-by-year controls when changing the reports.
- Heatmaps must state whether cells are totals, averages, medians, or maxima. The current local-time heatmaps use total message counts per weekday/hour bucket, with color scaled against the busiest bucket.
- Bar charts should render as SVG on desktop and as readable mobile bar lists on narrow screens. Do not reintroduce tiny mobile SVG axis labels or horizontal chart scrolling.
- Generated JSON stats should stay available beside the HTML outputs for programmatic use.
