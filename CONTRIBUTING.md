# Contributing

Bug reports and fixes are welcome, for example a chart that reads wrong or an export shape the generators don't handle. Use synthetic data in issues, tests and examples: no real ChatGPT exports, extracted transcripts, snippets of real conversations, generated reports, stats JSON or screenshots of private reports.

## Three ways to contribute

I want your time to go into pull requests that don't collide with someone else's work and that I can accept. So every contribution takes one of these paths:

- **Small fix**: a bug fix, a typo, a docs improvement or a similar small change needs no issue. Open a pull request straight away. If an issue for it already exists, claim that issue first as described in [Solve an issue](#solve-an-issue).
- **Existing issue**: claim it with `/take` and wait for the assignment before you open a pull request (see [Solve an issue](#solve-an-issue)).
- **New feature**: start a thread in the [Ideas discussions](https://github.com/martinfrancois/chatgpt-wrapped/discussions/categories/ideas). Once I approve the feature there, I open an issue for it, and you claim that issue with `/take`. Please don't open a pull request for a feature without an approved issue that is assigned to you.

## Solve an issue

I add the `help wanted` label to issues where community contributions are especially welcome and the `good first issue` label for tasks suited to new contributors.

Before you start on an issue, check that nobody is assigned to it and that no open pull request already addresses it. The issue's **Development** section in the sidebar lists the linked pull requests. Then claim it by commenting `/take` on a line of its own, and wait until you're assigned before you open a pull request. If the issue has the `help wanted` or `good first issue` label, nobody holds it and no open pull request addresses it, a bot assigns it to you right away; otherwise it replies with the reason. Nobody can take an issue that someone else holds, and you can hold up to 3 issues at a time (set in the [claim workflow](.github/workflows/claim-issue.yml)). For issues without those labels, leave a comment asking if you can work on it and I'll get back to you. If you ask in plain words on a labelled issue ("can I take this?"), the bot answers once and tells you whether `/take` would work.

If you haven't opened a pull request for the issue 14 days after you were assigned (or after your last comment there), the bot asks in the issue whether you're still working on it. Reply there or open a pull request, a draft is fine; if neither happens within 7 days of that reminder, the bot unassigns you so someone else can take the issue. Once you have an open or merged pull request for the issue, there are no reminders. If your pull request is closed without merging, the 14 days start again from that day. The [claim reminders workflow](.github/workflows/claim-reminders.yml) sets both waiting times. If you stop working on an issue, say so in a comment so someone else can take it.

A pull request that closes an issue its author doesn't hold, or one that another open pull request already addresses, gets a comment and the `claim conflict` label.

## Run the checks

Set up a virtual environment as in the README's [Setup](README.md#setup), then run what CI runs:

```bash
python -m py_compile extract_chatgpt_markdown.py generate_report.py generate_wrapped.py
python -m pytest
chatgpt-wrapped-report examples/synthetic-export --output-dir output/example-report --timezone UTC
chatgpt-wrapped-all-time examples/synthetic-export --output-dir output/example-wrapped --timezone UTC
```

For a change to the reports, open both generated HTML files and check them in a narrow and a wide window. [AGENTS.md](AGENTS.md) lists the report wording and layout rules.

**Coffee on me.** If you've had a pull request merged here and we meet in person, at a conference or anywhere else, tell me your GitHub handle and I'll buy you a coffee or a drink. It's one per person, however many pull requests you've made. It's a thank-you, not payment: the project doesn't pay for contributions or offer bounties.
