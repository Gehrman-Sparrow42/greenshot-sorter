import os
import time
import re
import shutil
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configuration
SOURCE_DIR = r"D:\tools\GREENSHOT DB"
UNSORTED_DIR = os.path.join(SOURCE_DIR, "Unsorted")
LOG_FILE = os.path.join(SOURCE_DIR, "greenshot_sorter.log")

# Custom Keywords Dictionary
CUSTOM_KEYWORDS = {
    "Bloodborne": "Bloodborne",
    "shadPS4": "Bloodborne",
}

# Browser Keywords
BROWSER_KEYWORDS = ["Ablaze Fl", "Chrome", "Firefox", "TikTok", "Edge", "Opera", "Brave"]

def setup_logging():
    # Ensure source directory exists before trying to create a log file
    os.makedirs(SOURCE_DIR, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

def sanitize_folder_name(name):
    """Removes illegal Windows characters from the folder name."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
    return sanitized.strip('. ')

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
            # Renaming a file to itself is a solid, non-destructive way to check if it's locked on Windows
            os.rename(file_path, file_path)
            return True
        except PermissionError:
            time.sleep(delay)
        except OSError:
            time.sleep(delay)
    return False

def process_file(file_path):
    """Processes a newly created file according to the priority rules."""
    filename = os.path.basename(file_path)
    
    # 0. Strict Prefix Verification
    # Expected format: YYYY-MM-DD HH_MM_SS-Window Title.png
    prefix_pattern = r'^(\d{4}-\d{2}-\d{2} \d{2}_\d{2}_\d{2})-(.*)\.png$'
    match = re.match(prefix_pattern, filename, re.IGNORECASE)
    
    if not match:
        # Move to Unsorted if it doesn't match the required prefix
        os.makedirs(UNSORTED_DIR, exist_ok=True)
        if wait_for_file_release(file_path):
            new_filename = get_unique_filename(UNSORTED_DIR, filename)
            dest_file = os.path.join(UNSORTED_DIR, new_filename)
            try:
                shutil.move(file_path, dest_file)
                logging.info(f"Moved invalid format file to Unsorted: {filename}")
            except Exception as e:
                logging.error(f"Failed to move {filename} to Unsorted: {e}")
        else:
            logging.warning(f"Timeout waiting for file release (Unsorted): {filename}")
        return

    # Extract title from the regex match
    title = match.group(2).strip()
    dest_folder_name = None
    parent_folder = None
    
    # Priority 1: Manga & Webtoons (Regex)
    
    # Match "Chapter" or "Episode", optional number, optional dash/colon, then the rest
    match = re.search(r'(?i)\b(Chapter|Episode)\b\s*(\d+[\.\d]*)?\s*(?:[-:|]\s*)?(.*)', title)
    
    if match:
        prefix = title[:match.start()].strip()
        suffix = match.group(3).strip()
        
        # Failsafe: check if the prefix contains enough letters to be a real title
        letters_in_prefix = len(re.sub(r'[^a-zA-Z]', '', prefix))
        
        if letters_in_prefix >= 2:
            dest_folder_name = prefix
        else:
            # Prefix is junk (e.g. "11 _ ", "(S2)"). The title is probably in the suffix.
            clean_suffix = suffix
            suffixes_to_strip = [
                "MangaDex", "Opera", "Chrome", "Firefox", "Edge", "Brave", "TikTok", "Ablaze Fl",
                "Asura Scans", "Reaper Scans", "Flame Scans", "Luminous Scans", "Webtoon", 
                "Bilibili", "Tapas", "Tappytoon", "MangaPlus", "Bato.to", "Google Search"
            ]
            # Iteratively strip known browser/site suffixes from the end
            strip_pattern = r'(?i)\s*[-|]\s*(' + '|'.join(suffixes_to_strip) + r')\b.*$'
            
            while True:
                new_suffix = re.sub(strip_pattern, '', clean_suffix).strip()
                if new_suffix == clean_suffix:
                    break
                clean_suffix = new_suffix
            
            if clean_suffix:
                dest_folder_name = clean_suffix
            else:
                dest_folder_name = "Unknown Manga"
                
        parent_folder = "Manga"
    
    # Priority 2: Custom Keywords & Special Apps
    if not dest_folder_name:
        if "potplayer" in title.lower():
            # PotPlayer Specific Logic
            video_name = re.sub(r'(?i)\s*[-|]?\s*potplayer\s*[-|]?\s*', '', title).strip()
            video_name = re.sub(r'(?i)\.(mkv|mp4|avi|webm)$', '', video_name).strip()
            
            # Clean up episode numbers and release groups so related videos group together
            clean_show_name = video_name
            clean_show_name = re.sub(r'^\[.*?\]\s*', '', clean_show_name) # Strip leading [Group]
            clean_show_name = re.sub(r'\s*[\[\(].*?[\]\)]\s*$', '', clean_show_name) # Strip trailing tags
            clean_show_name = re.sub(r'(?i)\s+[-]?\s*(e\d+|\d{2,4}v?\d?)\s*$', '', clean_show_name).strip() # Strip episode
            
            dest_folder_name = clean_show_name if clean_show_name else "Unknown Content"
            parent_folder = "PotPlayer"
        else:
            for keyword, mapped_folder in CUSTOM_KEYWORDS.items():
                if keyword.lower() in title.lower():
                    dest_folder_name = mapped_folder
                    break
                
    # Priority 3: Browser Catch-All
    if not dest_folder_name:
        for browser in BROWSER_KEYWORDS:
            if browser.lower() in title.lower():
                dest_folder_name = "Browser Screenshots"
                break
                
    # Priority 4: Default / Clean Games
    if not dest_folder_name:
        # Regex to strip trailing version numbers or trainer info (e.g. "v1.5.80.0", "v1.0-v1.1", "Plus 14 Trainer")
        clean_title = re.sub(r'(?i)\s+(v\d[\d\.\-v]*|Plus\s+\d+\s+Trainer.*)$', '', title).strip()
        if clean_title:
            dest_folder_name = clean_title
        else:
            dest_folder_name = "Unknown Game"

    # Sanitize and finalize the folder name
    dest_folder_name = sanitize_folder_name(dest_folder_name)
    if not dest_folder_name: # Fallback if sanitization completely empties the name
        dest_folder_name = "Unknown"
        
    if parent_folder:
        dest_path = os.path.join(SOURCE_DIR, parent_folder, dest_folder_name)
    else:
        dest_path = os.path.join(SOURCE_DIR, dest_folder_name)
    os.makedirs(dest_path, exist_ok=True)
    
    # Wait for the file to be fully released before moving
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
        # We only care about PNG files created in the monitored directory
        if not event.is_directory and event.src_path.lower().endswith('.png'):
            # Triggered immediately on file creation
            logging.info(f"New file detected: {event.src_path}")
            try:
                process_file(event.src_path)
            except Exception as e:
                logging.error(f"Unhandled error processing {event.src_path}: {e}")
                
    def on_moved(self, event):
        # Handle edge cases where a tool saves a temp file and renames it into the directory
        if not event.is_directory and event.dest_path.lower().endswith('.png'):
            if os.path.dirname(event.dest_path) == SOURCE_DIR:
                logging.info(f"File moved into directory: {event.dest_path}")
                try:
                    process_file(event.dest_path)
                except Exception as e:
                    logging.error(f"Unhandled error processing {event.dest_path}: {e}")

def process_existing_files():
    """Scans the SOURCE_DIR for existing .png files and processes them."""
    logging.info("Scanning for existing files to sort...")
    if not os.path.exists(SOURCE_DIR):
        return
        
    for item in os.listdir(SOURCE_DIR):
        file_path = os.path.join(SOURCE_DIR, item)
        if os.path.isfile(file_path) and item.lower().endswith('.png'):
            try:
                process_file(file_path)
            except Exception as e:
                logging.error(f"Unhandled error processing existing file {file_path}: {e}")

def main():
    setup_logging()
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
    except KeyboardInterrupt:
        observer.stop()
        logging.info("Stopping Greenshot Sorter via KeyboardInterrupt")
    observer.join()

if __name__ == "__main__":
    main()
