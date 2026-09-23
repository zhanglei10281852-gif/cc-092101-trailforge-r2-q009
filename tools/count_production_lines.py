from __future__ import annotations

import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "trailforge"


def effective_lines(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    ignored_rows: set[int] = set()
    reader = io.StringIO(source).readline
    for token in tokenize.generate_tokens(reader):
        if token.type == tokenize.COMMENT:
            ignored_rows.update(range(token.start[0], token.end[0] + 1))
        if token.type == tokenize.STRING and token.start[1] == 0:
            ignored_rows.update(range(token.start[0], token.end[0] + 1))
    count = 0
    for number, line in enumerate(source.splitlines(), start=1):
        if line.strip() and number not in ignored_rows:
            count += 1
    return count


def main() -> int:
    files = sorted(PACKAGE.rglob("*.py"))
    total = 0
    for path in files:
        count = effective_lines(path)
        total += count
        print(f"{count:5d} {path.relative_to(ROOT)}")
    print(f"TOTAL {total}")
    return 0 if total >= 5000 else 1


if __name__ == "__main__":
    raise SystemExit(main())
