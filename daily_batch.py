"""Durable queue and once-per-Istanbul-day Flash scheduling."""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from routing import Route

STATE_FILE = Path(__file__).resolve().parent / ".flash_batch.json"
ISTANBUL = timezone(timedelta(hours=3))
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ISTANBUL).date().isoformat()


def _load():
    if not STATE_FILE.exists():
        return {"roots": {}}
    with STATE_FILE.open(encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or not isinstance(state.get("roots"), dict):
        raise TypeError("Invalid Flash queue state")
    return state


def _save(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=STATE_FILE.parent,
        prefix=".flash-batch-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _root(state, root, date):
    return state["roots"].setdefault(
        str(Path(root).resolve()), {"last_run": date, "pending": {}}
    )


def enqueue(root, path, title, route):
    """Persist an unresolved capture; duplicate paths update the same queue entry."""
    path = Path(path).resolve()
    from sorter import fingerprint

    expected = list(fingerprint(path))
    with _LOCK:
        state = _load()
        entry = _root(state, root, today())
        entry["pending"][str(path)] = {
            "title": title,
            "parts": list(route.parts),
            "reason": route.reason,
            "fingerprint": expected,
        }
        _save(state)


def recover(root, unsorted, router):
    """Recover captures whose move finished before their queue update was saved."""
    from sorter import (
        IMAGE_EXTENSIONS,
        classify_file,
        destination,
        fingerprint,
        regular_entry,
    )

    folders = {Path(unsorted), Path(root) / "Browser" / "Unsorted"}
    folders.update(
        Path(root) / "Browser" / "Manga" / site for site in router.site_aliases.values()
    )
    with _LOCK:
        state = _load()
        entry = _root(state, root, today())
        changed = False
        for folder in folders:
            if not folder.is_dir() or folder.resolve() != folder:
                continue
            for path in folder.iterdir():
                try:
                    if path.suffix.lower() not in IMAGE_EXTENSIONS or not regular_entry(
                        path
                    ):
                        continue
                    title, route = classify_file(path, router)
                    if title and not route.unresolved:
                        continue
                    if destination(Path(root), Path(unsorted), route) != folder:
                        continue
                    item = {
                        "title": title,
                        "parts": list(route.parts),
                        "reason": route.reason,
                        "fingerprint": list(fingerprint(path)),
                    }
                    if entry["pending"].get(str(path)) != item:
                        entry["pending"][str(path)] = item
                        changed = True
                except FileNotFoundError:
                    continue  # Another local move finished while recovery was scanning.
        if changed:
            _save(state)


def claim(root, *, now=None, force=False):
    """Claim the current day before work starts; return a snapshot, or None if not due."""
    date = today(now)
    with _LOCK:
        state = _load()
        key = str(Path(root).resolve())
        new_root = key not in state["roots"]
        entry = _root(state, root, date)
        if not force and entry["last_run"] >= date:
            if new_root:
                _save(state)
            return None
        entry["last_run"] = date
        pending = dict(entry["pending"])
        _save(state)
        return pending


def run(root, unsorted, router, pending, classifier):
    """Process a claimed snapshot, retaining failed or unresolved entries for tomorrow."""
    # Imported here to keep the queue usable independently of the watcher.
    from sorter import destination, fingerprint, move_capture, regular_entry

    items = []
    for name, item in pending.items():
        path = Path(name)
        try:
            resolved = path.resolve()
            allowed = resolved.is_relative_to(
                Path(root).resolve()
            ) or resolved.is_relative_to(Path(unsorted).resolve())
            if (
                not allowed
                or not regular_entry(path)
                or list(fingerprint(path)) != item.get("fingerprint")
            ):
                _remove(root, name, item)
                continue
        except FileNotFoundError:
            _remove(root, name, item)
            continue
        except OSError:
            logger.exception("Cannot inspect queued capture %s; will retry", path)
            continue
        items.append((name, item))
    for start in range(0, len(items), 30):
        group = items[start : start + 30]
        if not classifier.prepare_titles(item["title"] for _, item in group):
            return
        _process_group(
            root,
            unsorted,
            group,
            classifier,
            destination,
            fingerprint,
            move_capture,
            regular_entry,
        )


def _process_group(
    root,
    unsorted,
    group,
    classifier,
    destination,
    fingerprint,
    move_capture,
    regular_entry,
):
    for name, item in group:
        path = Path(name)
        try:
            if not regular_entry(path):
                continue
            expected = fingerprint(path)
            if list(expected) != item.get("fingerprint"):
                _remove(root, name, item)
                continue
            local = Route(tuple(item["parts"]), item["reason"], True)
            route = classifier.resolve(item["title"], path, local)
            if route.unresolved:
                continue
            folder = destination(root, unsorted, route)
            target = (
                path if folder == path.parent else move_capture(path, folder, expected)
            )
            if target is None:
                continue
            _remove(root, name, item)
            logger.info("Flash filed %s -> %s", path.name, target)
        except Exception:
            logger.exception("Flash batch could not file %s; will retry", path)


def _remove(root, name, item):
    with _LOCK:
        state = _load()
        queue = state["roots"][str(Path(root).resolve())]["pending"]
        if queue.get(name) == item:
            del queue[name]
            _save(state)
