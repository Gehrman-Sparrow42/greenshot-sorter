import json
import tempfile
import unittest
from pathlib import Path

from routing import Routing, safe_component


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Known Game").mkdir()
        (self.root / "Greenshot").mkdir()
        (self.root / "Known Game Chapter 7").mkdir()
        (self.root / "Browser" / "Manga" / "Example Series").mkdir(parents=True)
        self.routing = Routing(self.root)

    def test_representative_routes(self):
        cases = [
            ("Greenshot", (), True),
            ("MangaDex - Firefox", ("Browser", "Manga", "MangaDex"), True),
            ("PotPlayer", ("PotPlayer",), True),
            (
                "A.Sample.Show.S02E04.Title.1080p.WEB-DL",
                ("Media", "A Sample Show"),
                False,
            ),
            (
                "Sample Question - Google Gemini - Firefox",
                ("Browser", "AI", "Google Gemini"),
                False,
            ),
            (
                "Fantasy Chapter 12 - MangaDex - Firefox",
                ("Browser", "Manga", "Fantasy", "Chapter 12", "MangaDex"),
                False,
            ),
            (
                "Chapter ١٢ - Fantasy - MangaDex - Chrome",
                ("Browser", "Manga", "Fantasy", "Chapter 12", "MangaDex"),
                False,
            ),
            (
                "Fantasy Episode 3.5 - Asura Comic - Floorp",
                ("Browser", "Manga", "Fantasy", "Episode 3.5", "Asura Scans"),
                False,
            ),
            (
                "Fantasy Chapter 7 - Firefox",
                ("Browser", "Manga", "Fantasy", "Chapter 7"),
                False,
            ),
            ("[Group] Show - 03.mkv - PotPlayer", ("PotPlayer", "Show"), False),
            (
                "Bloodborne guide - YouTube - Firefox",
                ("Browser", "Gaming", "Bloodborne", "YouTube"),
                False,
            ),
            ("A video - YouTube - Chrome", ("Browser", "Video", "YouTube"), False),
            ("shadPS4", ("Bloodborne",), False),
            ("Known Game", ("Known Game",), False),
            ("Unfamiliar page - Firefox", ("Browser", "Unsorted"), True),
            ("Unfamiliar app", (), True),
            (
                "Example Show Episode 08 – PotPlayer",
                ("PotPlayer", "Example Show"),
                False,
            ),
            (
                "Example Series – MangaDex – Firefox",
                ("Browser", "Manga", "Example Series", "MangaDex"),
                False,
            ),
            (
                "Example Series Volume ٢ - MangaDex - Chrome",
                ("Browser", "Manga", "Example Series", "Volume 2", "MangaDex"),
                False,
            ),
            (
                "Example Series Vol. 3 - MangaDex",
                ("Browser", "Manga", "Example Series", "Volume 3", "MangaDex"),
                False,
            ),
            (
                "Example Episode 5 - YouTube - Firefox",
                ("Browser", "Video", "YouTube"),
                False,
            ),
            (
                "Example Series Chapter 7",
                ("Browser", "Manga", "Example Series", "Chapter 7"),
                False,
            ),
            (
                "(S2) Episode 6 _ Example Series - Opera",
                ("Browser", "Manga", "Example Series", "Season 2 Episode 6"),
                False,
            ),
            (
                "Read Online Example Series Chapter 5 - MangaDex - Firefox",
                ("Browser", "Manga", "Example Series", "Chapter 5", "MangaDex"),
                False,
            ),
            (
                "Example Channel - Twitch - Firefox",
                ("Browser", "Video", "Twitch"),
                False,
            ),
            ("Example - Reddit - Firefox", ("Browser", "Social", "Reddit"), False),
            (
                "Example - Wikipedia - Firefox",
                ("Browser", "Reference", "Wikipedia"),
                False,
            ),
            (
                "Example - Google Search - Firefox",
                ("Browser", "Search", "Google Search"),
                False,
            ),
            ("Known Game walkthrough - Firefox", ("Browser", "Unsorted"), True),
            (
                "Bloodborne guide - Unknown Site - Firefox",
                ("Browser", "Gaming", "Bloodborne"),
                False,
            ),
            ("Known Game Build 123", ("Known Game",), False),
            ("Known Game Chapter 7", ("Known Game Chapter 7",), False),
            ("A new Arc of History", (), True),
            ("On the Edge of 2049", (), True),
            ("2049 - PotPlayer", ("PotPlayer", "2049"), False),
            (
                "Blade Runner 2049 - PotPlayer",
                ("PotPlayer", "Blade Runner 2049"),
                False,
            ),
            (
                "A Bloodborne guide - Reddit - Chrome",
                ("Browser", "Gaming", "Bloodborne", "Reddit"),
                False,
            ),
            (
                "東京物語 Ch. 1.5 - MangaDex - Firefox",
                ("Browser", "Manga", "東京物語", "Chapter 1.5", "MangaDex"),
                False,
            ),
            (
                "86 Chapter 2 - MangaDex",
                ("Browser", "Manga", "86", "Chapter 2", "MangaDex"),
                False,
            ),
            ("shadPS4 v0.5 - running game", ("Bloodborne",), False),
            (
                "Episode 12 _ Example Series - Opera",
                ("Browser", "Manga", "Example Series", "Episode 12"),
                False,
            ),
            ("", (), True),
        ]
        for title, parts, unresolved in cases:
            with self.subTest(title=title):
                route = self.routing.classify(title)
                self.assertEqual((route.parts, route.unresolved), (parts, unresolved))

    def test_source_precedes_subject(self):
        self.assertEqual(
            self.routing.classify("Bloodborne Chapter 2 - PotPlayer").parts,
            ("PotPlayer", "Bloodborne Chapter 2"),
        )

    def test_corrections_and_aliases(self):
        path = self.root / "corrections.json"
        path.write_text(
            json.dumps(
                {
                    "exact_titles": {"A title": ["Custom", "Folder"]},
                    "series_aliases": {"Short Name": "Example Series"},
                    "site_aliases": {"Example Reader": "Example Site"},
                }
            ),
            encoding="utf-8",
        )
        routing = Routing(self.root, path)
        self.assertEqual(routing.classify("a TITLE").parts, ("Custom", "Folder"))
        self.assertEqual(
            routing.classify("Short Name Chapter 4 - Example Reader - Chrome").parts,
            ("Browser", "Manga", "Example Series", "Chapter 4", "Example Site"),
        )
        self.assertEqual(routing.canonical_series("example series"), "Example Series")
        self.assertEqual(routing.canonical_site("example reader"), "Example Site")

    def test_unsafe_correction_rejected(self):
        path = self.root / "corrections.json"
        path.write_text('{"exact_titles": {"x": [".."]}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            Routing(self.root, path)
        for part in ("", "Unsorted"):
            path.write_text(
                json.dumps({"exact_titles": {"x": [part]}}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                Routing(self.root, path)

    def test_windows_components(self):
        for value in ("", "..", "CON", "con.txt", "NUL", "COM¹"):
            with self.subTest(value=value):
                self.assertEqual(safe_component(value), "")
        self.assertEqual(safe_component("  Some: name. "), "Some name")
        self.assertEqual(len(safe_component("x" * 300)), 255)


if __name__ == "__main__":
    unittest.main()
