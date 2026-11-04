import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import daily_batch
from routing import Route, Routing


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "archive"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "state.json"
        self.patcher = patch.object(daily_batch, "STATE_FILE", self.state)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.route = Route(("Browser", "Unsorted"), "unknown", True)
        self.path = self.root / "image.png"
        self.path.write_bytes(b"capture")

    def test_persistence_and_midnight_catchup(self):
        with patch.object(daily_batch, "today", return_value="2026-11-04"):
            daily_batch.enqueue(self.root, self.path, "title", self.route)
            daily_batch.enqueue(self.root, self.path, "title", self.route)
        self.assertIsNone(
            daily_batch.claim(
                self.root, now=datetime(2026, 11, 4, 20, 59, tzinfo=timezone.utc)
            )
        )
        pending = daily_batch.claim(
            self.root, now=datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        )
        self.assertEqual(len(pending), 1)
        self.assertIsNone(
            daily_batch.claim(
                self.root, now=datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(
            len(
                daily_batch.claim(
                    self.root,
                    now=datetime(2026, 11, 5, 10, tzinfo=timezone.utc),
                    force=True,
                )
            ),
            1,
        )

    def test_recovery_after_move_before_queue_save(self):
        folder = self.root / "Browser" / "Unsorted"
        folder.mkdir(parents=True)
        path = folder / "2026-11-04 12_00_00-Mystery - Firefox.png"
        path.write_bytes(b"image")
        router = Routing(self.root)
        daily_batch.recover(self.root, self.root / "Unsorted", router)
        self.assertEqual(len(daily_batch.claim(self.root, force=True)), 1)
        daily_batch.recover(self.root, self.root / "Unsorted", router)
        self.assertEqual(len(daily_batch.claim(self.root, force=True)), 1)

    def test_retry_and_collision(self):
        daily_batch.enqueue(self.root, self.path, "title", self.route)
        pending = daily_batch.claim(self.root, force=True)
        classifier = Mock()
        classifier.prepare_titles.return_value = True
        classifier.resolve.return_value = self.route
        daily_batch.run(
            self.root, self.root / "Unsorted", Routing(self.root), pending, classifier
        )
        self.assertEqual(len(daily_batch.claim(self.root, force=True)), 1)
        folder = self.root / "Browser" / "Docs"
        folder.mkdir(parents=True)
        (folder / self.path.name).write_bytes(b"existing")
        classifier.resolve.return_value = Route(("Browser", "Docs"), "resolved")
        daily_batch.run(
            self.root, self.root / "Unsorted", Routing(self.root), pending, classifier
        )
        self.assertEqual((folder / "image_1.png").read_bytes(), b"capture")
        self.assertEqual((folder / "image.png").read_bytes(), b"existing")
        self.assertEqual(daily_batch.claim(self.root, force=True), {})

    def test_missing_and_replaced_files_are_pruned_before_model_call(self):
        other = self.root / "missing.png"
        other.write_bytes(b"gone")
        daily_batch.enqueue(self.root, self.path, "old title", self.route)
        daily_batch.enqueue(self.root, other, "missing", self.route)
        pending = daily_batch.claim(self.root, force=True)
        other.unlink()
        self.path.write_bytes(b"a replacement with different size")
        classifier = Mock()
        daily_batch.run(
            self.root, self.root / "Unsorted", Routing(self.root), pending, classifier
        )
        classifier.prepare_titles.assert_not_called()
        self.assertEqual(daily_batch.claim(self.root, force=True), {})

    def test_bulk_failure_retains_queue(self):
        daily_batch.enqueue(self.root, self.path, "title", self.route)
        pending = daily_batch.claim(self.root, force=True)
        classifier = Mock()
        classifier.prepare_titles.return_value = False
        daily_batch.run(
            self.root, self.root / "Unsorted", Routing(self.root), pending, classifier
        )
        classifier.resolve.assert_not_called()
        self.assertEqual(len(daily_batch.claim(self.root, force=True)), 1)


if __name__ == "__main__":
    unittest.main()
