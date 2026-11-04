"""Watch the Greenshot inbox, or preview local routing across an existing archive."""

import argparse
import csv
import ctypes
import errno
import hashlib
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import daily_batch
from routing import Route, Routing

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parent
SOURCE_DIR = Path(os.getenv("GREENSHOT_SOURCE_DIR", PROJECT.parent / "GREENSHOT DB"))
LOG_FILE = Path(os.getenv("GREENSHOT_LOG_FILE", PROJECT / "greenshot_sorter.log"))
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
CAPTURE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}[_:-]\d{2}[_:-]\d{2}\s*[-_]?\s*(.*)$")


def classify_file(path, router):
    match = CAPTURE.fullmatch(path.stem)
    title = match[1].strip() if match else ""
    if not title:
        return "", Route((), "Missing Greenshot timestamp or window title")
    return title, router.classify(title)


def destination(root, unsorted, route):
    if not route.parts:
        return unsorted
    folder = root.joinpath(*route.parts).resolve()
    if not folder.is_relative_to(root) or folder == root:
        raise ValueError("Route leaves the archive or points back into the inbox")
    return folder


def regular_entry(path):
    info = path.lstat()
    return (
        stat.S_ISREG(info.st_mode)
        and not getattr(info, "st_file_attributes", 0)
        & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def preview(archive, unsorted, router, stream):
    counts = Counter()
    writer = csv.writer(stream)
    writer.writerow(
        ("filename", "current_folder", "proposed_folder", "local_decision", "reason")
    )
    for directory, dirs, names in os.walk(archive):
        base = Path(directory)
        dirs[:] = sorted(
            d
            for d in dirs
            if not (
                getattr((base / d).lstat(), "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        )
        for name in sorted(names):
            path = base / name
            if path.suffix.lower() not in IMAGE_EXTENSIONS or not regular_entry(path):
                continue
            # Our collision suffix is metadata when an original sibling confirms it.
            stem = re.sub(r"_\d+$", "", path.stem)
            original = path.with_name(stem + path.suffix)
            _, route = classify_file(original if original.is_file() else path, router)
            target = destination(archive, unsorted, route)
            relative = (
                target.relative_to(archive)
                if target.is_relative_to(archive)
                else target
            )
            status = "needs Flash" if route.unresolved else "local"
            writer.writerow(
                (name, base.relative_to(archive), relative, status, route.reason)
            )
            counts["images"] += 1
            counts["different folder"] += target != base
            counts["needs Flash"] += route.unresolved
    return counts


def fingerprint(path):
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def exclude_writers(path):
    """Keep a Windows handle that allows reading and moving, but excludes writers."""
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    open_file.restype = wintypes.HANDLE
    handle = open_file(str(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(wintypes.HANDLE(handle))


def stable_capture(path, timeout=5, interval=0.25):
    previous = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = fingerprint(path)
        if current == previous and current[1]:
            try:
                with exclude_writers(path):
                    return current
            except PermissionError:
                pass
        previous = current
        time.sleep(interval)
    return None


def publish_unique(source, folder, filename):
    """Publish within a volume without replacing an existing capture."""
    suffix = Path(filename).suffix
    stem = Path(filename).stem
    index = 0
    while True:
        target = folder / (filename if index == 0 else f"{stem}_{index}{suffix}")
        try:
            if os.name == "nt":
                # Windows rename fails if the target already exists.
                source.rename(target)
            else:
                os.link(source, target)
                source.unlink()
            return target
        except FileExistsError:
            index += 1


def move_capture(path, folder, expected):
    with exclude_writers(path):
        if not regular_entry(path) or fingerprint(path) != expected:
            return None  # The inbox is rescanned and will classify the replacement.
        folder.mkdir(parents=True, exist_ok=True)
        try:
            return publish_unique(path, folder, path.name)
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                raise
        # An externally configured Unsorted folder can be on another volume.
        # Finish copying before making the final filename visible.
        with tempfile.NamedTemporaryFile(
            dir=folder, prefix=".greenshot-", suffix=".part", delete=False
        ) as tmp:
            staging = Path(tmp.name)
        try:
            shutil.copy2(path, staging)
            target = publish_unique(staging, folder, path.name)
            try:
                path.unlink()
            except OSError:
                # The source still exists. Roll back our copy before the retry.
                target.unlink()
                raise
            return target
        finally:
            staging.unlink(missing_ok=True)


class Inbox(FileSystemEventHandler):
    def __init__(self, root, unsorted, router, flash):
        super().__init__()
        self.root = root
        self.unsorted = unsorted
        self.router = router
        self.flash = flash
        self.local_worker = ThreadPoolExecutor(1, thread_name_prefix="greenshot-local")
        self.ai_worker = ThreadPoolExecutor(1, thread_name_prefix="greenshot-flash")
        self.pending = set()
        self.lock = threading.Lock()

    def submit(self, path):
        path = Path(path)
        if path.parent != self.root or path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        with self.lock:
            if path in self.pending:
                return
            self.pending.add(path)
        self.local_worker.submit(self.process, path)

    def scan(self):
        for path in self.root.iterdir():
            self.submit(path)

    def on_created(self, event):
        if not event.is_directory:
            self.submit(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.submit(event.dest_path)

    def finish(self, path):
        with self.lock:
            self.pending.discard(path)

    def process(self, path):
        try:
            if not regular_entry(path):
                return
            expected = stable_capture(path)
            if expected is None:
                logger.warning("File still being written; will retry: %s", path.name)
                return
            title, route = classify_file(path, self.router)
            target = self.move(path, expected, route)
            if target and route.unresolved:
                daily_batch.enqueue(self.root, target, title, route)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Could not process %s; will retry", path.name)
        finally:
            self.finish(path)

    def schedule_batch(self):
        if not self.flash:
            return
        daily_batch.recover(self.root, self.unsorted, self.router)
        pending = daily_batch.claim(self.root)
        if pending is not None:
            self.ai_worker.submit(
                daily_batch.run,
                self.root,
                self.unsorted,
                self.router,
                pending,
                self.flash,
            )

    def move(self, path, expected, route):
        target = move_capture(
            path, destination(self.root, self.unsorted, route), expected
        )
        if target:
            logger.info("Moved %s -> %s (%s)", path.name, target, route.reason)
        return target

    def close(self):
        self.local_worker.shutdown(wait=True)
        self.ai_worker.shutdown(wait=True)


@contextmanager
def single_watcher(root):
    if os.name != "nt":
        yield True
        return
    name = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    mutex = create(None, False, "Local\\GreenshotSorter-" + name)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    finally:
        kernel.CloseHandle(wintypes.HANDLE(mutex))


def watch(root, unsorted, router):
    from flash_classifier import FlashClassifier

    enabled = os.getenv("GREENSHOT_FLASH", "1").lower() not in {"0", "off", "false"}
    inbox = Inbox(root, unsorted, router, FlashClassifier(router) if enabled else None)
    observer = Observer()
    observer.schedule(inbox, str(root), recursive=False)
    observer.start()
    try:
        logger.info(
            "Watching %s (daily Flash %s)", root, "enabled" if enabled else "disabled"
        )
        while True:
            inbox.scan()
            inbox.schedule_batch()
            time.sleep(30)  # Recover from locks, replacements and missed OS events.
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        inbox.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preview-archive",
        type=Path,
        nargs="?",
        const=SOURCE_DIR,
        help="preview local routes, defaulting to the configured archive",
    )
    parser.add_argument(
        "--report", type=Path, help="new preview CSV outside the archive"
    )
    parser.add_argument(
        "--run-flash-batch",
        action="store_true",
        help="process the pending queue now and mark today as run",
    )
    args = parser.parse_args()
    root = (args.preview_archive or SOURCE_DIR).resolve()
    unsorted = Path(os.getenv("GREENSHOT_UNSORTED_DIR", root / "Unsorted")).resolve()
    if root.is_relative_to(unsorted):
        parser.error("Unsorted must be a subfolder of, or separate from, the inbox")
    corrections = Path(os.getenv("GREENSHOT_CORRECTIONS", PROJECT / "corrections.json"))
    if args.preview_archive and args.run_flash_batch:
        parser.error("--run-flash-batch cannot be combined with preview")
    if args.preview_archive:
        if not root.is_dir():
            parser.error("archive directory does not exist")
        router = Routing(root, corrections if corrections.exists() else None)
        if args.report:
            report = args.report.resolve()
            if any(
                report.is_relative_to(p) for p in (root, unsorted, SOURCE_DIR.resolve())
            ):
                parser.error("report must be outside the archive and Unsorted")
            with report.open("x", newline="", encoding="utf-8-sig") as stream:
                counts = preview(root, unsorted, router, stream)
        else:
            sys.stdout.reconfigure(errors="backslashreplace")
            counts = preview(root, unsorted, router, sys.stdout)
        print(
            f"{counts['images']} images, {counts['different folder']} different folders, "
            f"{counts['needs Flash']} need Flash, 0 model calls.",
            file=sys.stderr,
        )
        return
    if args.report:
        parser.error("--report requires --preview-archive")
    with single_watcher(root) as acquired:
        if acquired:
            root.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            logging.basicConfig(
                level=logging.INFO,
                handlers=[handler],
                format="%(asctime)s - %(levelname)s - %(message)s",
            )
            router = Routing(root, corrections if corrections.exists() else None)
            if args.run_flash_batch:
                from flash_classifier import FlashClassifier

                pending = daily_batch.claim(root, force=True)
                daily_batch.run(
                    root, unsorted, router, pending, FlashClassifier(router)
                )
            else:
                watch(root, unsorted, router)
        else:
            parser.error("watcher or batch already running for this inbox")


if __name__ == "__main__":
    main()
