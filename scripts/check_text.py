"""Fail if any tracked text file contains an em dash (U+2014) or en dash (U+2013).

House style: plain punctuation only, in code, docs, and every string the bot sends.
Run: uv run python scripts/check_text.py
"""

import subprocess
import sys
from pathlib import Path

BANNED = {chr(0x2014): "em dash", chr(0x2013): "en dash"}
SKIP = {"uv.lock"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [Path(p) for p in out.splitlines() if p and p not in SKIP]


def main() -> int:
    problems = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for char, name in BANNED.items():
                if char in line:
                    problems.append(f"{path}:{lineno}: {name}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} banned dash(es) found. Use a comma, colon, period or hyphen.")
        return 1
    print("check_text: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
