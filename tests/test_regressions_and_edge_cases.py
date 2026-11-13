"""Pytest regression suite for greenshot_sorter using tmp_path fixtures only."""
import io
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import daily_batch
import flash_classifier
import sorter
from routing import Route, Routing


def test_daily_batch_recovers_same_day_after_failure_and_cooldown(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    unsorted = tmp_path / "unsorted"
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(daily_batch, "STATE_FILE", state_file)

    capture = archive / "2026-11-04 12_00_00-Mystery - Firefox.png"
    capture.write_bytes(b"png-bytes")
    route = Route(("Browser", "Unsorted"), "unknown", True)

    day1 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(daily_batch, "today", lambda now=None: (now or day1).astimezone(timezone.utc).date().isoformat())
    daily_batch.enqueue(archive, capture, "Mystery", route)

    pending = daily_batch.claim(archive, now=day2)
    assert pending is not None and len(pending) == 1

    failing_classifier = Mock()
    failing_classifier.prepare_titles.return_value = False
    daily_batch.run(archive, unsorted, Routing(archive), pending, failing_classifier, now=day2)

    # Immediate re-claim respects short retry backoff, NOT 24h self-lockout
    assert daily_batch.claim(archive, now=day2 + timedelta(seconds=10)) is None

    # After short backoff expires, same-day claim succeeds again
    retry_time = day2 + timedelta(seconds=daily_batch.RETRY_BACKOFF_BASE_SECONDS + 5)
    recovered_pending = daily_batch.claim(archive, now=retry_time)
    assert recovered_pending is not None and len(recovered_pending) == 1

    # Also test cooldown during _process_group releases claim cleanly for same-day recovery
    cooldown_classifier = Mock()
    cooldown_classifier.prepare_titles.return_value = True
    cooldown_classifier.retry_at = time.monotonic() + 300
    cooldown_classifier.resolve.return_value = route
    daily_batch.run(archive, unsorted, Routing(archive), recovered_pending, cooldown_classifier, now=retry_time)

    later_same_day = retry_time + timedelta(seconds=daily_batch.RETRY_BACKOFF_BASE_SECONDS * 2 + 5)
    third_pending = daily_batch.claim(archive, now=later_same_day)
    assert third_pending is not None and len(third_pending) == 1

    # Once run finishes cleanly without errors/cooldown, same-day re-claim stops until next day
    ok_classifier = Mock()
    ok_classifier.prepare_titles.return_value = True
    ok_classifier.retry_at = 0
    ok_classifier.resolve.return_value = Route(("Browser", "Docs"), "resolved")
    daily_batch.run(archive, unsorted, Routing(archive), third_pending, ok_classifier, now=later_same_day)
    assert daily_batch.claim(archive, now=later_same_day + timedelta(hours=1)) is None


def test_load_env_file_parses_quotes_and_preserves_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\n'
        'GREENSHOT_TEST_A="C:/Temp/One"\n'
        "GREENSHOT_TEST_B='value_two'\n"
        "export GREENSHOT_TEST_C=plain_val\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GREENSHOT_TEST_A", raising=False)
    monkeypatch.delenv("GREENSHOT_TEST_B", raising=False)
    monkeypatch.setenv("GREENSHOT_TEST_C", "already_set")

    loaded = sorter.load_env_file(env_file)
    assert loaded == {
        "GREENSHOT_TEST_A": "C:/Temp/One",
        "GREENSHOT_TEST_B": "value_two",
        "GREENSHOT_TEST_C": "plain_val",
    }
    assert os.environ["GREENSHOT_TEST_A"] == "C:/Temp/One"
    assert os.environ["GREENSHOT_TEST_B"] == "value_two"
    assert os.environ["GREENSHOT_TEST_C"] == "already_set"


def test_routing_handles_truncated_potplayer_and_nhentai_librewolf_titles(tmp_path):
    router = Routing(tmp_path)

    pot_trunc = router.classify("Example.Show.S01E05.1080p.mkv - Pot")
    assert pot_trunc.parts == ("PotPlayer", "Example Show")
    assert pot_trunc.unresolved is False

    mp4_trunc = router.classify("[Group] Sample Anime - 04.mp4 -")
    assert mp4_trunc.parts == ("PotPlayer", "Sample Anime")
    assert mp4_trunc.unresolved is False

    nh_series = router.classify("My Favorite Doujin - nhentai — Li")
    assert nh_series.parts == ("Browser", "Manga", "My Favorite Doujin", "nhentai")
    assert nh_series.unresolved is False

    nh_only = router.classify("... nhentai — Li")
    assert nh_only.parts == ("Browser", "Manga", "nhentai")
    assert nh_only.unresolved is True


def test_sorter_destination_max_path_and_logged_missing_file(tmp_path, monkeypatch, caplog):
    archive = tmp_path / "archive"
    archive.mkdir()
    unsorted = tmp_path / "unsorted"

    # Relative root must still validate cleanly
    monkeypatch.chdir(tmp_path)
    folder = sorter.destination(Path("archive"), unsorted, Route(("Browser", "Docs"), "ok"))
    assert folder == (archive / "Browser" / "Docs").resolve()

    # MAX_PATH bounded filename truncation keeps total path <= MAX_TARGET_PATH_CHARS
    deep_folder = archive / ("sub_" * 10)
    deep_folder.mkdir(parents=True)
    src = archive / "source_capture.png"
    src.write_bytes(b"data")
    long_name = "A" * 240 + ".png"
    published = sorter.publish_unique(src, deep_folder, long_name)
    assert published.exists()
    if os.name == "nt":
        assert len(str(published)) <= sorter.MAX_TARGET_PATH_CHARS

    # Missing file inside Inbox.process logs a warning instead of silently swallowing
    inbox = sorter.Inbox(archive, unsorted, Routing(archive), None)
    missing = archive / "2026-11-04 12_00_00-Vanished.png"
    with caplog.at_level(logging.WARNING, logger="sorter"):
        inbox.process(missing)
    assert any("vanished" in rec.message.lower() or "inaccessible" in rec.message.lower() for rec in caplog.records)


def test_flash_temporary_directory_uses_ignore_cleanup_errors(monkeypatch, tmp_path):
    captured = {}

    class DummyTmp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(flash_classifier.tempfile, "TemporaryDirectory", DummyTmp)

    class DummyProc:
        stdin = Mock()
        stdout = io.StringIO('{"result":{"stopReason":"end_turn"}}\n')

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(flash_classifier.subprocess, "Popen", lambda *a, **k: DummyProc())
    monkeypatch.setattr(flash_classifier, "_executable", lambda: "codex")
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        flash_classifier._request("hi", timeout=1)
    assert captured.get("ignore_cleanup_errors") is True
