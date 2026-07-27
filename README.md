# Greenshot Image Sorter

A lightweight, highly robust background Python script that monitors a specified directory for new Greenshot screenshots and automatically sorts them into dedicated folders based on customizable priority rules.

## Features

- **Zero Resource Footprint**: Uses the OS native file event system (`watchdog`), so it consumes 0% CPU while idle.
- **Failproof Mechanisms**: 
  - Checks for Windows file locks before moving to prevent `PermissionError` from incomplete writes.
  - Automatically sanitizes illegal OS characters `< > : " / \ | ? *` in folder names.
  - Prevents file overwrites by appending `_1`, `_2`, etc. to duplicate file names.
- **Smart Priority Sorting Logic**:
  - **Priority 1 (Manga/Webtoons)**: Automatically extracts the manga series name by parsing "Chapter" or "Episode". It handles edge cases by identifying scanlation group prefixes or browser suffixes (e.g. MangaDex, Opera, Asura Scans).
  - **Priority 2 (PotPlayer)**: Captures PotPlayer screenshots and groups them into a `PotPlayer` main folder, dynamically creating subfolders stripped of `.mkv` or release tags.
  - **Priority 3 (Browser Catch-All)**: Moves screenshots containing typical browser names to a single `Browser Screenshots` directory.
  - **Priority 4 (Games & Default)**: Fallback logic that strips common game trainer versions (e.g., `v1.0-v1.1`, `Plus 14 Trainer`) and files them neatly by game name.
- **Seamless Recovery on Boot**: Automatically processes any newly found unsorted screenshots whenever the script starts up.

## Installation & Setup

1. Install Python 3.
2. Install the required packages: `pip install -r requirements.txt`.
3. Open `sorter.py` and modify the `SOURCE_DIR` variable to point to your screenshot save folder.
4. Run `start_sorter.bat` to launch the watcher completely silently in the background using `pythonw.exe`.

To make the script start automatically on PC boot, place a `.vbs` shortcut or launch script into your Windows `Startup` folder.
