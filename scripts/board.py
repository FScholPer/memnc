"""Generate the board block at the top of docs/roadmap.md from the Part I tables.

The Part I rows are the source of truth. The board is a short view of them for
readers who need to find the next task quickly, including small models.

    python scripts/board.py          rewrite the block between the markers
    python scripts/board.py --check  exit 1 if the block is out of date

States follow the A2A task lifecycle (v1.0, TASK_STATE_*) where one exists.
DEFERRED has no A2A equivalent: the task is kept but not scheduled.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "docs" / "roadmap.md"
START = "<!-- board:start -->"
END = "<!-- board:end -->"

STATE = {"☐": "SUBMITTED", "🟡": "WORKING", "✅": "COMPLETED", "🗑": "CANCELED", "⏸": "DEFERRED"}
ROW = re.compile(r"^\| (T\d+\.\d+) \| (.+?) \| (☐|🟡|✅|🗑|⏸) \| (.*) \|$")
PRIORITY = re.compile(r"\*\*(P[0-2])\*\*")
OPEN = {"SUBMITTED", "WORKING"}


def tasks(text: str) -> list[tuple[str, str, str, str]]:
    """(id, title, state, priority) for every Part I row, in document order."""
    part1 = text.split("## Part I", 1)[1].split("## Part II", 1)[0]
    found = []
    for line in part1.splitlines():
        match = ROW.match(line)
        if not match:
            continue
        task_id, title, status, evidence = match.groups()
        prio = PRIORITY.search(evidence)
        found.append(
            (task_id, title.replace("`", "").strip(), STATE[status], prio.group(1) if prio else "")
        )
    return found


def table(rows: list[tuple[str, str, str, str]]) -> list[str]:
    out = ["| ID | Task | State |", "|---|---|---|"]
    for task_id, title, state, _ in rows:
        short = title if len(title) <= 90 else title[:87] + "..."
        out.append(f"| {task_id} | {short} | {state} |")
    return out


def render(text: str) -> str:
    items = tasks(text)
    lines = [
        START,
        "",
        "## Board",
        "",
        "Generated from the Part I tables by `scripts/board.py`. Edit the rows, not this block.",
        "",
        "- `SUBMITTED` planned · `WORKING` in progress · `COMPLETED` done and verified",
        "- `CANCELED` dropped · `DEFERRED` kept, not scheduled (no A2A equivalent)",
        "",
    ]
    sections = [
        ("P0 — now", lambda s, p: s in OPEN and p == "P0"),
        ("P1 — next", lambda s, p: s in OPEN and p == "P1"),
        ("P2 — later", lambda s, p: s in OPEN and p == "P2"),
        ("Unranked", lambda s, p: s in OPEN and p == ""),
        ("Deferred", lambda s, p: s == "DEFERRED"),
    ]
    for label, pick in sections:
        rows = [item for item in items if pick(item[2], item[3])]
        lines.append(f"### {label} ({len(rows)})")
        lines.append("")
        if rows:
            lines.extend(table(rows))
        else:
            lines.append("None.")
        lines.append("")
    done = [item[0] for item in items if item[2] == "COMPLETED"]
    dropped = [item[0] for item in items if item[2] == "CANCELED"]
    lines.append(f"### Completed ({len(done)})")
    lines.append("")
    lines.append(", ".join(done) if done else "None.")
    lines.append("")
    lines.append(f"### Canceled ({len(dropped)})")
    lines.append("")
    lines.append(", ".join(dropped) if dropped else "None.")
    lines.append("")
    lines.append(END)
    return "\n".join(lines)


def updated(text: str) -> str:
    block = render(text)
    if START in text and END in text:
        pattern = re.escape(START) + r".*?" + re.escape(END)
        return re.sub(pattern, lambda _m: block, text, count=1, flags=re.S)
    return text.replace("## Contents", block + "\n\n## Contents", 1)


def main(argv: list[str]) -> int:
    text = ROADMAP.read_text(encoding="utf-8")
    new = updated(text)
    if "--check" in argv:
        if new != text:
            print("docs/roadmap.md: board block is out of date; run scripts/board.py", file=sys.stderr)
            return 1
        return 0
    if new != text:
        ROADMAP.write_text(new, encoding="utf-8")
        print("board block updated")
    else:
        print("board block already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
