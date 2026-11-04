# Greenshot Sorter

A Windows background watcher that files Greenshot captures from the top level of the screenshot archive. It uses the window title, optional personal corrections and, for ambiguous captures, Gemini Flash Medium through the official Antigravity CLI.

## Where screenshots go

| Window title | New folder |
| --- | --- |
| Example Series Chapter 12 – MangaDex – Firefox | Browser / Manga / Example Series / Chapter 12 / MangaDex |
| Example Series – MangaDex – Firefox | Browser / Manga / Example Series / MangaDex |
| Bloodborne guide – YouTube – Firefox | Browser / Gaming / Bloodborne / YouTube |
| A video – YouTube | Browser / Video / YouTube |
| Example Show Episode 08 – PotPlayer | PotPlayer / Example Show |
| shadPS4 | Bloodborne |

A browser name identifies the application. A site name needs its own evidence. Missing chapter and site levels are omitted. Browser captures with insufficient evidence go in `Browser/Unsorted`; other unresolved captures go in the configured Unsorted folder. Images with missing Greenshot timestamps or empty titles also use that folder. Original filenames are preserved, with a numeric suffix on collisions.

## Run or preview

Use the existing Greenshot Sorter launcher or run `start_sorter.bat`. To run from a console in this workspace:

```powershell
& C:\Tools\.venv\Scripts\python.exe C:\Tools\greenshot_sorter\sorter.py
```

The watcher scans loose images on startup and periodically checks for files left open by their writer. It watches only the inbox root. Windows permits one watcher per resolved inbox per login session. Supported capture formats are PNG, JPEG, BMP and WebP.

To preview the existing archive, run the following in the project directory. The first command prints CSV rows. The second creates a new UTF-8 CSV at a path outside the archive. Choose a fresh report filename each time.

```powershell
& C:\Tools\.venv\Scripts\python.exe sorter.py --preview-archive
& C:\Tools\.venv\Scripts\python.exe sorter.py --preview-archive --report "$env:TEMP\greenshot-preview.csv"
```

The preview records the current folder, proposed folder, reason and whether a live capture would need Flash. It runs local rules only and leaves archived captures in place. A different folder may reflect a new layout or an earlier manual choice.

## Teach the sorter a correction

Copy `corrections.example.json` to `corrections.json`, edit it, then restart the watcher. Use `exact_titles` to map a complete window title to an array of folder names under the archive. Use `series_aliases` to map alternate series spellings to the same name while retaining the detected installment, or `site_aliases` to map alternate reader-site names. The local corrections file is excluded from Git.

## Flash fallback

The watcher tries local rules first. An unresolved capture goes to a separate worker so new, recognizable screenshots can still be filed. The worker uses the official signed-in Antigravity CLI and selects `gemini-3.8-flash-medium`. It asks about the title before attaching an image. For unresolved inputs, visible labels in the screenshot can supply evidence. The Python sorter validates the proposed fields and chooses the destination. If the service times out or is unavailable, the capture uses its local fallback.

Archive preview and unit tests run without an Antigravity account. To use live Flash, install the official Windows CLI and sign in interactively under the same Windows user that runs the watcher. For local-only sorting, set `GREENSHOT_FLASH=0` in the launch environment.

## Settings

The sorter reads Windows environment variables at startup. `.env.example` lists examples; set the values in the environment used by the launcher.

| Variable | Default |
| --- | --- |
| `GREENSHOT_SOURCE_DIR` | `GREENSHOT DB` beside the project folder |
| `GREENSHOT_UNSORTED_DIR` | `Unsorted` under the inbox |
| `GREENSHOT_LOG_FILE` | `greenshot_sorter.log` beside the script |
| `GREENSHOT_CORRECTIONS` | `corrections.json` beside the script, when present |
| `GREENSHOT_FLASH` | `1` (enabled for live ambiguous captures) |

Install `requirements.txt` in the Python environment running the launcher. Run the offline regression suite from the project directory with `python -m unittest discover -v`.
