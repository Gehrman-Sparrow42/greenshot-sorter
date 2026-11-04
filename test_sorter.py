import csv
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sorter
from routing import Route, Routing


class WatcherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "archive"
        self.root.mkdir()
        self.unsorted = self.root.parent / "external"
        self.router = Routing(self.root)

    def capture(self, title, data=b"test image"):
        path = self.root / f"2026-11-04 12_00_00-{title}.png"
        path.write_bytes(data)
        return path

    def test_collision_preserves_both_files(self):
        path = self.capture("shadPS4")
        target = self.root / "Bloodborne"
        target.mkdir()
        (target / path.name).write_bytes(b"original")
        moved = sorter.move_capture(path, target, sorter.fingerprint(path))
        self.assertEqual(moved.name, path.stem + "_1.png")
        self.assertEqual(moved.read_bytes(), b"test image")
        self.assertEqual((target / path.name).read_bytes(), b"original")

    def test_external_unsorted_and_same_name_replacement(self):
        path = self.capture("")
        expected = sorter.fingerprint(path)
        path.write_bytes(b"replacement")
        self.assertIsNone(sorter.move_capture(path, self.unsorted, expected))
        self.assertTrue(path.exists())
        _, route = sorter.classify_file(path, self.router)
        self.assertEqual(
            sorter.destination(self.root, self.unsorted, route), self.unsorted
        )
        inbox = sorter.Inbox(self.root, self.unsorted, self.router, None)
        inbox.submit(path)
        inbox.close()
        self.assertEqual((self.unsorted / path.name).read_bytes(), b"replacement")

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_open_writer_is_not_ready(self):
        path = self.capture("shadPS4")
        with path.open("ab") as handle:
            handle.write(b"partial")
            handle.flush()
            self.assertIsNone(sorter.stable_capture(path, timeout=0.15, interval=0.02))
        self.assertEqual(
            sorter.stable_capture(path, timeout=0.15, interval=0.02),
            sorter.fingerprint(path),
        )

    def test_duplicate_events_and_local_work_during_flash(self):
        waiting, release = threading.Event(), threading.Event()

        def resolve(title, path, route):
            waiting.set()
            release.wait(10)
            return Route(("Browser", "Docs"), "synthetic response")

        flash = Mock()
        flash.resolve.side_effect = resolve
        inbox = sorter.Inbox(self.root, self.unsorted, self.router, flash)
        unknown = self.capture("Unrecognized - Firefox")
        try:
            inbox.submit(unknown)
            inbox.scan()
            self.assertTrue(waiting.wait(5))
            known = self.capture("shadPS4")
            inbox.scan()
            target = self.root / "Bloodborne" / known.name
            deadline = time.monotonic() + 5
            while not target.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(target.exists())
            self.assertTrue(unknown.exists())
        finally:
            release.set()
            inbox.close()
        self.assertTrue((self.root / "Browser" / "Docs" / unknown.name).exists())
        flash.resolve.assert_called_once()

    def test_preview_read_only_and_report_outside_archive(self):
        path = self.capture("shadPS4")
        original = [(p, sorter.fingerprint(p)) for p in self.root.iterdir()]
        report = io.StringIO()
        counts = sorter.preview(self.root, self.unsorted, self.router, report)
        self.assertEqual(counts["images"], 1)
        self.assertEqual(
            next(csv.DictReader(io.StringIO(report.getvalue())))["proposed_folder"],
            "Bloodborne",
        )
        result = subprocess.run(
            [
                sys.executable,
                sorter.__file__,
                "--preview-archive",
                self.root,
                "--report",
                self.root / "report.csv",
            ],
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            original, [(p, sorter.fingerprint(p)) for p in self.root.iterdir()]
        )
        self.assertTrue(path.exists())

    def test_cross_volume_copy(self):
        path = self.capture("unknown")
        publish = sorter.publish_unique

        def cross_volume(src, folder, name):
            if src == path:
                raise OSError(sorter.errno.EXDEV, "another volume")
            return publish(src, folder, name)

        with patch.object(sorter, "publish_unique", side_effect=cross_volume):
            target = sorter.move_capture(path, self.unsorted, sorter.fingerprint(path))
        self.assertEqual(target.read_bytes(), b"test image")
        self.assertFalse(path.exists())
        self.assertEqual(list(self.unsorted.iterdir()), [target])

    @unittest.skipUnless(os.name == "nt", "Windows single instance guard")
    def test_duplicate_watcher_guard(self):
        with (
            sorter.single_watcher(self.root) as first,
            sorter.single_watcher(self.root) as second,
        ):
            self.assertTrue(first)
            self.assertFalse(second)
        with sorter.single_watcher(self.root) as later:
            self.assertTrue(later)


if __name__ == "__main__":
    unittest.main()
