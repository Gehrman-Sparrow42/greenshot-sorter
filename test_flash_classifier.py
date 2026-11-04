import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from flash_classifier import FlashClassifier, _parse
from routing import Route, Routing


def answer(subject="", installment="", site="", category=""):
    return json.dumps(
        {
            "category": category,
            "subject": subject,
            "installment": installment,
            "site": site,
            "quotes": {
                "category": subject,
                "subject": subject,
                "installment": installment,
                "site": site,
            },
        }
    )


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.router = Routing(self.root)
        self.local = Route(("Browser", "Unsorted"), "ambiguous", True)
        self.image = self.root / "one.png"
        Image.new("RGB", (4, 4), "red").save(self.image)

    def test_same_generic_title_different_images(self):
        calls = []

        def request(prompt, image=None, **kwargs):
            calls.append(image)
            if image is None:
                return answer()
            color = Image.open(io.BytesIO(image)).getpixel((0, 0))
            return answer(
                "Alpha" if color[0] > color[2] else "Beta",
                "Chapter 4",
                "Example",
                "Manga",
            )

        classifier = FlashClassifier(self.router, request=request)
        other = self.root / "two.bmp"
        Image.new("RGB", (4, 4), "blue").save(other)
        self.assertEqual(
            classifier.resolve("Browser", self.image, self.local).parts[2], "Alpha"
        )
        self.assertEqual(
            classifier.resolve("Browser", other, self.local).parts[2], "Beta"
        )
        classifier.resolve("Browser", self.image, self.local)
        self.assertEqual(len(calls), 3)

    def test_malformed_timeout_and_unreadable_return_local(self):
        for request in (
            lambda *a, **k: "bad json",
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError()),
        ):
            classifier = FlashClassifier(self.router, request=request)
            self.assertEqual(
                classifier.resolve("Browser", self.image, self.local), self.local
            )
            self.assertGreater(classifier.retry_at, 0)
        classifier = FlashClassifier(self.router, request=lambda *a, **k: answer())
        self.assertEqual(
            classifier.resolve("Browser", self.root / "missing.png", self.local),
            self.local,
        )

    def test_title_evidence_and_paths(self):
        self.assertEqual(
            _parse(answer("../escape", "Chapter 1", category="Manga"), "Browser"), {}
        )
        self.assertEqual(_parse(answer("invented", category="Manga"), "Browser"), {})
        classifier = FlashClassifier(
            self.router,
            request=lambda *a, **k: answer(
                "Moonlit Teapot", "Episode 8", category="Manga"
            ),
        )
        result = classifier.resolve("Moonlit Teapot Episode 8", self.image, self.local)
        self.assertEqual(
            result.parts, ("Browser", "Manga", "Moonlit Teapot", "Episode 8")
        )

    def test_generic_subject_requires_image_and_cannot_be_used_as_subject(self):
        calls = []

        def request(prompt, image=None, **kwargs):
            calls.append(image)
            return (
                answer("Greenshot", category="Application")
                if image is None
                else answer("Greenshot", category="Gaming")
            )

        classifier = FlashClassifier(self.router, request=request)
        self.assertEqual(
            classifier.resolve("Greenshot", self.image, self.local), self.local
        )
        self.assertEqual(len(calls), 2)

    def test_native_and_browser_categories_preserve_context(self):
        classifier = FlashClassifier(
            self.router, request=lambda *a, **k: answer("My Game", category="Gaming")
        )
        native = Route((), "unknown", True)
        self.assertEqual(
            classifier.resolve("My Game", self.image, native).parts,
            ("Games", "My Game"),
        )
        self.assertEqual(
            classifier.resolve("My Game", self.image, self.local).parts,
            ("Browser", "Gaming", "My Game"),
        )

    def test_partial_manga_site_and_player_are_preserved(self):
        classifier = FlashClassifier(
            self.router,
            request=lambda *a, **k: answer(
                "Moonlit Teapot", "Chapter 9", category="Manga"
            ),
        )
        partial = Route(
            ("Browser", "Manga", "MangaDex"), "manga site without series", True
        )
        self.assertEqual(
            classifier.resolve("Moonlit Teapot Chapter 9", self.image, partial).parts,
            ("Browser", "Manga", "Moonlit Teapot", "Chapter 9", "MangaDex"),
        )
        player = FlashClassifier(
            self.router, request=lambda *a, **k: answer("A Film", category="Media")
        )
        self.assertEqual(
            player.resolve(
                "A Film", self.image, Route(("PotPlayer",), "player", True)
            ).parts,
            ("PotPlayer", "A Film"),
        )

    def test_image_can_identify_browser_from_a_generic_native_title(self):
        local = self.router.classify("Greenshot")

        def request(prompt, image=None, **kwargs):
            if image is None:
                return answer()
            return answer("Example Series", "Chapter 8", "MangaDex", "Manga")

        result = FlashClassifier(self.router, request=request).resolve(
            "Greenshot", self.image, local
        )
        self.assertEqual(
            result.parts,
            ("Browser", "Manga", "Example Series", "Chapter 8", "MangaDex"),
        )
        self.assertEqual(result.reason, "Flash image evidence")

    def test_unreadable_image_does_not_suspend_provider(self):
        self.image.write_bytes(b"invalid PNG data")
        classifier = FlashClassifier(
            self.router, request=lambda *args, **kwargs: answer()
        )
        self.assertEqual(
            classifier.resolve("New tab", self.image, self.local), self.local
        )
        self.assertEqual(classifier.retry_at, 0)

    def test_resolved_local_route_never_calls_provider(self):
        classifier = FlashClassifier(
            self.router, request=lambda *a, **k: self.fail("unexpected request")
        )
        local = Route(("Known",), "local")
        self.assertEqual(classifier.resolve("Known", self.image, local), local)


if __name__ == "__main__":
    unittest.main()
