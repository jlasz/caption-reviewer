# Caption Reviewer

A local browser app for reviewing and editing image captions for LoRA and other image training datasets. Open a folder, inspect images beside their captions, edit tags and descriptions, and track review progress.

The app runs on your computer and binds to `127.0.0.1`. It includes no cloud services, telemetry, or remote scripts. No datasets, images, captions, model weights, or saved review history are included in this repository.

## Requirements

- Python 3.10 or newer.
- A modern desktop browser.
- Optional: [Pillow](https://pypi.org/project/Pillow/) to display TIFF images. Other supported formats use Python's standard library and the browser.

## Start reviewing

Download or clone this repository, then run from its folder:

```sh
python caption_reviewer.py "/path/to/your/dataset"
```

On systems where Python is named `python3`, use that command instead. On Windows, you can use:

```bat
py -3 caption_reviewer.py "path\to\your\dataset"
```

Or drag a dataset folder onto `run_caption_reviewer.bat`. The dataset can stay anywhere on your computer; it does not need to be copied into the tool folder.

The browser opens automatically. Keep the terminal open while reviewing, and press **Ctrl+C** in the terminal to stop. Use `--port 8000` to choose a local port, or `--no-browser` to open the printed address yourself.

For TIFF support:

```sh
python -m pip install Pillow
```

## Dataset layout

The app searches subfolders recursively. Each caption is a UTF-8 `.txt` file beside its image with the same base name. Missing caption files start empty and are created when you save.

Supported image extensions: PNG, JPG/JPEG, WebP, BMP, GIF, and TIF/TIFF. Display support for non-TIFF files depends on your browser.

Tags and description text can share one line:

```text
landscape, mountains, sunset. A mountain range is silhouetted against a warm evening sky.
```

The app also reads the older format with a blank line between tags and description. Inline descriptions are recognized after a period followed by an uppercase letter or digit, when the tag prefix contains a comma. Plain tag lists are supported too. Saving normalizes whitespace and writes tags and description on one line.

## Features

- Image preview with fit and actual-size modes.
- Separate tag and description editors with automatic saving, plus a manual Save button.
- Click a tag's remove button to delete it; use Undo to restore the last deleted tag.
- Filter by folder or filename, include matching caption text, or exclude matching text. The text filters can search everything, description only, or tags only.
- Search for `her left`, `his right`, or another supported pronoun/direction pair to reveal tools for swapping directions, changing one direction, or removing matching sentences from the current description.
- Track **Viewed (V)**, **Edited (E)**, and **Done (D)**. Double-click an already selected image entry to cycle its status.
- Move between unfinished images, mark the current image done, and resume progress later.
- Refresh the folder after external changes. Saves detect conflicting caption edits rather than silently replacing them.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+S | Save now |
| Ctrl+F | Search folder or filename |
| Ctrl+Shift+F | Search caption text |
| Alt+Left / Alt+Right | Previous / next unfinished image |
| Ctrl+PageUp / Ctrl+PageDown | Previous / next unfinished image |
| Alt+D | Mark done and advance |
| Ctrl+Z | Undo the last tag deletion when available |

## Local files and privacy

Edits are written directly to the selected dataset's caption files. Keep a backup of captions if you need to undo a review session.

Review progress is stored in `.caption-reviewer-state.json` in the selected dataset folder. A `.caption-reviewer.lock` file prevents two reviewer instances from editing the same dataset. These files stay local. External changes to an image or caption move a previously reviewed item back to Edited after a rescan.

The repository's `.gitignore` allows only the named tool and test files. Keep your datasets outside the repository. If you add new source files, add them explicitly to that allowlist.

The app is intended for use on your own computer. Do not expose its local server through a public proxy or tunnel.

## Development checks

Python integration tests use a temporary directory and generate their own sample data:

```sh
python -m unittest -v test_caption_reviewer.py
```

The JavaScript helper tests require Node.js with the built-in test runner:

```sh
node --test test_caption_direction_tools.js test_caption_filter_tools.js
```
