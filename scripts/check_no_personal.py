#!/usr/bin/env python3
"""Block personal information from entering the repo.

Decision 2026-11-16: no names, no personal setup details (hardware,
machine names, home paths), no quoted/dated personal attributions in
tracked content. Runs as part of the pre-commit hook and `make lint`.
"""
import re
import subprocess
import sys

# assembled at runtime so this file itself never contains the banned strings
_P = ("fr", "ank")
_H = ("dro", "picallus")
PATTERNS = [
    (r"(?i)\b" + _P[0] + _P[1] + r"\b", "personal name"),
    (r"(?i)\b" + _H[0] + "(?:" + _H[1] + "|" + _H[1].replace("pic", "bic") + r")\b", "GitHub handle"),
    (r"(?i)\bmaco\b", "machine name (use node-c)"),
    (r"(?i)\brx ?[67]\d{3,4}\b", "GPU model (use gpu-a/gpu-b)"),
    (r"(?i)\b780M\b", "iGPU model (use igpu)"),
    (r"/home/" + _P[0] + _P[1] + r"\b", "personal home path (use $HOME or container path)"),
    (r"(?i)\bki-station\b", "personal directory name"),
]

EXCLUDE_PREFIXES = ("workspace/", "data/", ".venv/", "scripts/check_no_personal.py")
TEXT_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".json", ".toml", ".txt", ".sh", ".cfg", ".ini")


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines()
            if f.endswith(TEXT_SUFFIXES) and not f.startswith(EXCLUDE_PREFIXES)]


def main():
    bad = False
    for path in staged_files():
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            for pattern, why in PATTERNS:
                if re.search(pattern, line):
                    print(f"{path}:{lineno}: banned personal info ({why}): {line.strip()[:120]}")
                    bad = True
    if bad:
        print("\nCommit blocked: personal information must not enter the repo "
              "(names, setup details, machine names, personal paths). "
              "Rewrite neutrally, e.g. 'the operator'.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
