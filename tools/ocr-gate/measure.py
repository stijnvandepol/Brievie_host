"""OCR gate: read the synthetic letters with Tesseract and count the facts found.

Usage: python measure.py IMAGES_DIR letters.json  (exit 1 below the threshold)
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
_WRITTEN = re.compile(r"\b(\d{1,2})\s{1,3}(" + "|".join(MONTHS) + r")\s{1,3}(\d{4})\b", re.IGNORECASE)
_NUMERIC = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
SENDER_RATIO = 0.9


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_dates(text: str) -> set[date]:
    candidates = [_date(int(y), MONTHS[m.lower()], int(d)) for d, m, y in _WRITTEN.findall(text)]
    candidates += [_date(int(y), int(m), int(d)) for d, m, y in _NUMERIC.findall(text)]
    return {found for found in candidates if found is not None}


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def sender_found(text: str, sender: str) -> bool:
    want = _norm(sender)
    for line in text.splitlines():
        if difflib.SequenceMatcher(None, _norm(line), want).ratio() >= SENDER_RATIO:
            return True
    return False


def passes(*, clean: tuple[int, int], scan: tuple[int, int]) -> bool:
    """(facts found of 36, deadlines found of 12) per variant."""
    clean_facts, clean_deadlines = clean
    scan_facts, scan_deadlines = scan
    return clean_facts >= 35 and clean_deadlines == 12 and scan_facts >= 33 and scan_deadlines >= 11


def ocr(path: Path) -> str:
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "nld+eng"],
        input=path.read_bytes(),
        capture_output=True,
        timeout=60,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def main(images_dir: str, letters_path: str) -> int:
    letters = json.loads(Path(letters_path).read_text(encoding="utf-8"))
    totals: dict[str, list[int]] = {"clean": [0, 0], "scan": [0, 0]}
    print(f"{'letter':<10} {'variant':<6} sender date deadline")
    for letter in letters:
        for variant in ("clean", "scan"):
            text = ocr(Path(images_dir) / f"{letter['id']}-{variant}.png")
            dates = parse_dates(text)
            sender = sender_found(text, letter["sender"])
            sent = date.fromisoformat(letter["date"]) in dates
            deadline = date.fromisoformat(letter["deadline"]) in dates
            totals[variant][0] += sender + sent + deadline
            totals[variant][1] += deadline
            print(f"{letter['id']:<10} {variant:<6} {int(sender):>6} {int(sent):>4} {int(deadline):>8}")
    clean = (totals["clean"][0], totals["clean"][1])
    scan = (totals["scan"][0], totals["scan"][1])
    ok = passes(clean=clean, scan=scan)
    print(f"clean: {clean[0]}/36 facts, {clean[1]}/12 deadlines")
    print(f"scan:  {scan[0]}/36 facts, {scan[1]}/12 deadlines")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
