"""Unit tests of the OCR gate's fact matching (no Tesseract needed)."""

import json
import re
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from measure import parse_dates, passes, sender_found  # noqa: E402


class MeasureTests(unittest.TestCase):
    def test_dates_parsed_in_all_forms(self) -> None:
        self.assertEqual(
            parse_dates("vóór 3 december 2026 en 05-01-2027"),
            {date(2026, 12, 3), date(2027, 1, 5)},
        )
        self.assertEqual(parse_dates("op 12/11/2026 en 4-2-2027"), {date(2026, 11, 12), date(2027, 2, 4)})
        self.assertEqual(parse_dates("31-02-2026"), set())

    def test_sender_fuzzy_match(self) -> None:
        self.assertTrue(sender_found("GEMEENTE V0orbeeldstad\nPostbus 1", "Gemeente Voorbeeldstad"))
        self.assertFalse(sender_found("Gemeente Andersdorp\nPostbus 1", "Gemeente Voorbeeldstad"))

    def test_threshold(self) -> None:
        self.assertIs(passes(clean=(35, 12), scan=(33, 11)), True)
        self.assertIs(passes(clean=(36, 11), scan=(36, 12)), False)
        self.assertIs(passes(clean=(36, 12), scan=(32, 12)), False)

    def test_letters_have_no_nine_digit_numbers(self) -> None:
        letters = json.loads((HERE / "letters.json").read_text(encoding="utf-8"))
        self.assertEqual(len(letters), 12)
        for letter in letters:
            for value in letter.values():
                text = json.dumps(value, ensure_ascii=False)
                digits = re.sub(r"[\s.\-/]", "", text)
                self.assertIsNone(re.search(r"\d{9}", digits), letter["id"])


if __name__ == "__main__":
    unittest.main()
