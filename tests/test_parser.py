from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser import parse_book

SAMPLES = Path(__file__).resolve().parent / "samples"


def _load(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


class ParserSamplePagesTest(unittest.TestCase):
    CATEGORY = "TestCategory"

    def test_full_metadata_page(self):
        rec = parse_book(_load("PageSample.html"), self.CATEGORY)

        self.assertEqual(rec["Category"], self.CATEGORY)
        self.assertEqual(rec["Title"], "Genghis Khan and the Making of the Modern World")
        self.assertEqual(rec["Authors"], "Weatherford, Jack")
        self.assertEqual(
            rec["Categories"],
            "Europe,Asia,Biography: historical, political and military,"
            "European history: middle ages,Asian history",
        )
        self.assertEqual(rec["PriceUSD"], 17.39)
        self.assertEqual(rec["PriceNIS"], 52.35)
        self.assertEqual(rec["Year"], 2005)
        self.assertEqual(rec["SynopsisLength"], len(rec["Synopsis"]))
        self.assertEqual(rec["SynopsisLength"], 952)
        self.assertTrue(rec["Synopsis"].startswith("NEW YORK TIMES BESTSELLER"))

        self.assertEqual(rec["NumberOfReviews"], 0)
        self.assertEqual(rec["StarRating"], "None")

        self.assertEqual(rec["Language"], "English")
        self.assertEqual(rec["Format"], "Paperback")
        self.assertEqual(rec["Dimensions"], "20.1,13.2,2.3")
        self.assertEqual(rec["DimensionsUnit"], "cm")
        self.assertEqual(rec["Weight"], 0.27)
        self.assertEqual(rec["WeightUnit"], "kg")
        self.assertEqual(rec["ISBN"], "0609809644")
        self.assertEqual(rec["ISBN13"], "9780609809648")

    def test_page_with_reviews(self):
        rec = parse_book(_load("PageSampleWithReviews.html"), self.CATEGORY)

        self.assertEqual(rec["Title"], "Japanese Cooking: A Simple art")
        self.assertEqual(
            rec["Authors"], "Tsuji, Shizuo,Fisher, M. F. K.,Reichl, Ruth"
        )
        self.assertEqual(rec["PriceUSD"], 38.25)
        self.assertEqual(rec["PriceNIS"], 115.14)
        self.assertEqual(rec["Year"], 2012)
        self.assertEqual(rec["SynopsisLength"], 1633)
        self.assertEqual(rec["SynopsisLength"], len(rec["Synopsis"]))

        self.assertEqual(rec["NumberOfReviews"], 2)
        self.assertEqual(rec["StarRating"], 5.0)
        self.assertIsInstance(rec["StarRating"], float)

        self.assertEqual(rec["Language"], "English")
        self.assertEqual(rec["Format"], "Hardcover")
        self.assertEqual(rec["Dimensions"], "25.9,18.8,4.3")
        self.assertEqual(rec["DimensionsUnit"], "cm")
        self.assertEqual(rec["Weight"], 1.45)
        self.assertEqual(rec["WeightUnit"], "kg")
        self.assertEqual(rec["ISBN"], "1568363885")
        self.assertEqual(rec["ISBN13"], "9781568363882")

    def test_page_priced_in_nis(self):
        rec = parse_book(_load("SamplePageNIS.html"), self.CATEGORY)

        # NIS-priced page: PriceNIS comes straight from the page, PriceUSD is
        # ceil2(PriceNIS / USD_RATE).
        self.assertEqual(rec["PriceNIS"], 66.74)
        self.assertEqual(rec["PriceUSD"], 22.18)
        self.assertEqual(rec["Year"], 2025)
        self.assertEqual(rec["Language"], "English")
        self.assertEqual(rec["Authors"], "Jérémy Mariez")
        self.assertEqual(rec["ISBN13"], "9782017276500")

    def test_page_with_missing_metadata(self):
        rec = parse_book(_load("PageSampleMissingMetadata.html"), self.CATEGORY)

        self.assertEqual(
            rec["Title"],
            "El Reino de Hierro: Auge y Caída de Prusia. 1600-1947 (in Spanish)",
        )
        self.assertEqual(rec["Authors"], "Christopher Clark")
        self.assertEqual(rec["Year"], 2021)
        self.assertEqual(rec["Language"], "Spanish")
        self.assertEqual(rec["Format"], "Paperback")
        self.assertEqual(rec["PriceUSD"], 39.35)
        self.assertEqual(rec["PriceNIS"], 118.45)
        self.assertEqual(rec["NumberOfReviews"], 10)
        self.assertEqual(rec["StarRating"], 4.2)
        self.assertEqual(rec["ISBN13"], "9788413841625")

        for absent in ("Dimensions", "DimensionsUnit", "Weight", "WeightUnit", "ISBN"):
            self.assertNotIn(absent, rec)

    def test_no_none_or_empty_values_in_any_record(self):
        for sample in (
            "PageSample.html",
            "PageSampleWithReviews.html",
            "PageSampleMissingMetadata.html",
            "SamplePageNIS.html",
        ):
            with self.subTest(sample=sample):
                rec = parse_book(_load(sample), self.CATEGORY)
                for value in rec.values():
                    self.assertIsNotNone(value)
                    if isinstance(value, str):
                        self.assertNotEqual(value, "")

    def test_price_nis_matches_usd_times_rate(self):
        from src.parser import USD_RATE, ceil2

        for sample in (
            "PageSample.html",
            "PageSampleWithReviews.html",
            "PageSampleMissingMetadata.html",
        ):
            with self.subTest(sample=sample):
                rec = parse_book(_load(sample), self.CATEGORY)
                self.assertEqual(rec["PriceNIS"], ceil2(rec["PriceUSD"] * USD_RATE))


if __name__ == "__main__":
    unittest.main()
