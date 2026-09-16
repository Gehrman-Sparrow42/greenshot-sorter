import os
import time
import re
import shutil
import logging
from logging.handlers import RotatingFileHandler
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configuration
SOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "GREENSHOT DB"))
if not os.path.exists(SOURCE_DIR):
    SOURCE_DIR = r"C:\Tools\GREENSHOT DB"

UNSORTED_DIR = os.path.join(SOURCE_DIR, "Unsorted")
LOG_FILE = os.path.join(os.path.dirname(__file__), "greenshot_sorter.log")

# Custom Keywords Dictionary (case-insensitive search -> destination folder)
CUSTOM_KEYWORDS = {
    "Bloodborne": "Bloodborne",
    "shadPS4": "Bloodborne",
    "V Rising": "VRising",
    "VRising": "VRising",
    "Persona 5 Royal": "Persona 5 Royal",
}

# Browser & Web Platform Keywords (routes to 'Browser Screenshots')
BROWSER_KEYWORDS = [
    "Floorp", "Ablaze Fl", "Ablaze Floorp", "Ablaze",
    "Chrome", "Google Chrome",
    "Firefox", "Mozilla Firefox",
    "Edge", "Microsoft Edge",
    "Opera", "Opera GX",
    "Brave", "Vivaldi", "Arc",
    "Zen Browser", "Zen",
    "Thorium", "LibreWolf", "Waterfox",
    "YouTube", "Twitch", "TikTok", "Reddit", "Twitter", "X.com", "Wikipedia", "Google Search"
]

# Manga site & browser suffixes to strip from manga titles
MANGA_SUFFIXES_TO_STRIP = [
    "MangaDex", "Opera", "Chrome", "Firefox", "Edge", "Brave", "TikTok",
    "Floorp", "Ablaze Fl", "Ablaze Floorp", "Ablaze", "Zen Browser", "Zen", "Vivaldi", "Arc",
    "Asura Scans", "Asura Comic", "Reaper Scans", "Flame Scans", "Flame Comics",
    "Luminous Scans", "Void Scans", "Drake Scans", "Night Scans", "Surya Scans",
    "Webtoon", "Webtoons", "Bilibili", "Tapas", "Tappytoon",
    "MangaPlus", "Bato.to", "Batoto", "Google Search", "YouTube",
    "Read Online", "ManhuaPlus", "MangaKakalot", "MangaNelo", "Vyvymanga",
    "MangaReader", "MangaPark", "Kun Manga", "Harimanga", "ToonGod", "Hivetoon"
]

# Supported Image Extensions
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


def setup_logging():
    """Configures rotating file logging with UTF-8 support."""
    os.makedirs(SOURCE_DIR, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    # Clear existing handlers if any
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.addHandler(handler)


def sanitize_folder_name(name):
    """Removes illegal Windows characters and cleans leading/trailing punctuation and spaces."""
    if not name:
        return ""
    # Strip illegal OS characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
    # Strip leading/trailing spaces, dots, dashes, underscores, colons
    sanitized = re.sub(r'[\s\.\-_:]+$', '', sanitized)
    sanitized = re.sub(r'^[\s\.\-_:]+', '', sanitized)
    # Collapse multiple consecutive spaces
    sanitized = re.sub(r'\s+', ' ', sanitized)
    return sanitized.strip()


def get_unique_filename(dest_path, filename):
    """Appends _1, _2, etc. if the file already exists in the destination."""
    name, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    while os.path.exists(os.path.join(dest_path, new_filename)):
        new_filename = f"{name}_{counter}{ext}"
        counter += 1
    return new_filename


def wait_for_file_release(file_path, timeout=5, delay=0.1):
    """Waits until the file is fully written and no longer locked by the OS."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Renaming a file to itself is a reliable, non-destructive check on Windows
            os.rename(file_path, file_path)
            return True
        except (PermissionError, OSError):
            time.sleep(delay)
    return False


def clean_manga_title(title):
    """Detects and extracts a clean manga/webtoon series name."""
    # Match Chapter / Episode / Ch / Ep / Vol / Volume / Capitulo / Cap
    match = re.search(
        r'(?i)\b(Chapter|Episode|Ch\.?|Ep\.?|Vol\.?|Volume|Capitulo|Cap\.?)\b\s*(\d+[\.\d]*)?\s*(?:[-:_|–—]\s*)?(.*)',
        title
    )
    if not match:
        return None

    prefix = title[:match.start()].strip()
    suffix = match.group(3).strip()

    # Strip common site/junk prefixes from the prefix
    prefix = re.sub(r'(?i)^\s*(Read|Read\s+Online|Raw)\s+', '', prefix).strip()
    prefix = re.sub(r'(?i)^\s*\(\s*(Season\s+\d+|S\d+)[^\)]*\)\s*', '', prefix).strip()
    prefix = re.sub(r'^[\s\-_:_–—]+', '', prefix).strip()

    # Count valid alphabetic characters in the prefix
    letters_in_prefix = len(re.sub(r'[^a-zA-Z]', '', prefix))
    if letters_in_prefix >= 2:
        return sanitize_folder_name(prefix)

    # Fallback to suffix if prefix was junk (e.g. "11 _ ", "(S2)")
    clean_suffix = suffix
    strip_pattern = r'(?i)\s*[-|_–—]\s*(' + '|'.join(re.escape(s) for s in MANGA_SUFFIXES_TO_STRIP) + r')\b.*$'
    while True:
        new_suffix = re.sub(strip_pattern, '', clean_suffix).strip()
        if new_suffix == clean_suffix:
            break
        clean_suffix = new_suffix

    clean_suffix = re.sub(r'(?i)^\s*(Read|Read\s+Online|Raw)\s+', '', clean_suffix).strip()
    clean_suffix = re.sub(r'(?i)^\s*\(\s*(Season\s+\d+|S\d+)[^\)]*\)\s*', '', clean_suffix).strip()
    clean_suffix = sanitize_folder_name(clean_suffix)
    return clean_suffix if clean_suffix else "Unknown Manga"


def clean_potplayer_title(title):
    """Extracts a clean show/video name from PotPlayer window title."""
    if "potplayer" not in title.lower():
        return None

    video_name = re.sub(r'(?i)\s*[-|_–—]?\s*potplayer\s*[-|_–—]?\s*', '', title).strip()
    video_name = re.sub(r'(?i)\.(mkv|mp4|avi|webm|ts|flv)$', '', video_name).strip()

    clean_show = video_name
    clean_show = re.sub(r'^\[.*?\]\s*', '', clean_show)  # Strip leading [Group]
    clean_show = re.sub(r'\s*[\[\(].*?[\]\)]\s*$', '', clean_show)  # Strip trailing tags / quality
    # Strip episode patterns (e.g., S01E08, Episode 08, E08, 08v2)
    clean_show = re.sub(r'(?i)\s*[-|_–—]?\s*(S\d+\s*E\d+|Episode\s*\d+|E\d+|\b\d{2,4}v?\d?\b)\s*.*$', '', clean_show).strip()
    clean_show = sanitize_folder_name(clean_show)
    return clean_show if clean_show else "Unknown Content"


def parse_destination(title):
    """Determines parent folder and destination folder based on priority rules."""
    if not title or not title.strip():
        return None, "Unsorted"

    title = title.strip()

    # Priority 1: Manga & Webtoons
    manga_name = clean_manga_title(title)
    if manga_name:
        return "manga", manga_name

    # Priority 2: PotPlayer
    potplayer_name = clean_potplayer_title(title)
    if potplayer_name:
        return "PotPlayer", potplayer_name

    # Priority 3: Custom Keywords
    for kw, mapped in CUSTOM_KEYWORDS.items():
        if kw.lower() in title.lower():
            return None, mapped

    # Priority 4: Browser & Web Platform Catch-All
    for browser in BROWSER_KEYWORDS:
        if browser.lower() in title.lower():
            return None, "Browser Screenshots"

    # Priority 5: Default Games / Clean Titles
    # Strip trailing version numbers, trainers, builds, or patches
    clean_title = re.sub(r'(?i)\s+(v\d[\d\.\-v]*|Plus\s+\d+\s+Trainer.*|Build\s+\d+.*|Patch\s+[\d\.]+.*)$', '', title).strip()
    clean_title = sanitize_folder_name(clean_title)
    if not clean_title:
        return None, "Unsorted"

    return None, clean_title


def process_file(file_path):
    """Processes a newly created or found image according to sorting rules."""
    filename = os.path.basename(file_path)

    # 0. Strict Prefix Verification
    # Format: YYYY-MM-DD HH_MM_SS-Window Title.ext (flexible on separators/spacing)
    prefix_pattern = r'^(\d{4}-\d{2}-\d{2}\s+\d{2}[_\-:]\d{2}[_\-:]\d{2})\s*[-_]?\s*(.*)\.(png|jpg|jpeg|bmp|webp)$'
    match = re.match(prefix_pattern, filename, re.IGNORECASE)

    if not match:
        dest_path = UNSORTED_DIR
        dest_folder_name = "Unsorted"
    else:
        title = match.group(2).strip()
        parent_folder, dest_folder_name = parse_destination(title)
        if parent_folder:
            dest_path = os.path.join(SOURCE_DIR, parent_folder, dest_folder_name)
        else:
            dest_path = os.path.join(SOURCE_DIR, dest_folder_name)

    os.makedirs(dest_path, exist_ok=True)

    if wait_for_file_release(file_path):
        new_filename = get_unique_filename(dest_path, filename)
        dest_file = os.path.join(dest_path, new_filename)
        try:
            shutil.move(file_path, dest_file)
            logging.info(f"Moved: {filename} -> {dest_folder_name}\\{new_filename}")
        except Exception as e:
            logging.error(f"Failed to move {filename} to {dest_folder_name}: {e}")
    else:
        logging.warning(f"Timeout waiting for file release: {filename}")


class ScreenshotHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(IMAGE_EXTENSIONS):
            logging.info(f"New file detected: {event.src_path}")
            try:
                process_file(event.src_path)
            except Exception as e:
                logging.error(f"Unhandled error processing {event.src_path}: {e}")

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.lower().endswith(IMAGE_EXTENSIONS):
            if os.path.dirname(os.path.abspath(event.dest_path)) == SOURCE_DIR:
                logging.info(f"File moved into directory: {event.dest_path}")
                try:
                    process_file(event.dest_path)
                except Exception as e:
                    logging.error(f"Unhandled error processing {event.dest_path}: {e}")


def process_existing_files():
    """Scans the SOURCE_DIR for existing loose image files and processes them."""
    if not os.path.exists(SOURCE_DIR):
        return

    for item in os.listdir(SOURCE_DIR):
        file_path = os.path.join(SOURCE_DIR, item)
        if os.path.isfile(file_path) and item.lower().endswith(IMAGE_EXTENSIONS):
            logging.info(f"Found existing loose file: {item}")
            try:
                process_file(file_path)
            except Exception as e:
                logging.error(f"Unhandled error processing existing file {file_path}: {e}")


def main():
    setup_logging()

    import sys
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

    logging.info(f"Starting Greenshot Sorter. Monitoring: {SOURCE_DIR}")

    process_existing_files()

    event_handler = ScreenshotHandler()
    observer = Observer()
    # recursive=False ensures we only monitor the root directory, not subfolders
    observer.schedule(event_handler, SOURCE_DIR, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopping Greenshot Sorter (Interrupt/Exit signal received)")
    except Exception as e:
        logging.error(f"Fatal error in main loop: {e}", exc_info=True)
    finally:
        observer.stop()
        observer.join()
        logging.info("Greenshot Sorter stopped.")


if __name__ == "__main__":
    main()
