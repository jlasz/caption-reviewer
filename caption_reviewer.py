"""Local browser-based image caption reviewer (Pillow is optional, for TIFF and grid thumbnails)."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import mimetypes
import os
import random
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from caption_reviewer_state import (
    IMAGE_EXTENSIONS,
    LOCK_FILENAME,
    STATE_FILENAME,
    caption_hash,
    image_signature,
    load_state,
    reviewer_is_running,
    save_state,
)


MAX_CAPTION_BYTES = 10 * 1024 * 1024
MAX_ADD_TAGS_BYTES = 10 * 1024 * 1024
THUMBNAIL_SIZES = (128, 192, 256, 384, 512, 768)
THUMBNAIL_CACHE_BYTES = 256 * 1024 * 1024
INLINE_DESCRIPTION_RE = re.compile(r"\.\s+(?=[A-Z0-9])")
LEGACY_DESCRIPTION_RE = re.compile(r"\n\s*\n")


# Grid mode adds tags with the same rules as the dataset tool manual.py when it
# sits beside this file (activation and @artist placement, deduplication,
# probability, randomized placement). Standalone copies use the simple fallback.
try:
    from manual import (  # type: ignore[import-not-found]
        append_tags_to_tag_list as _append_tags_to_tag_list,
        partition_tags_for_caption as _partition_tags_for_caption,
    )

    TAG_LOGIC = "manual.py"
except ImportError:
    _append_tags_to_tag_list = None
    _partition_tags_for_caption = None
    TAG_LOGIC = "built-in"


def split_tag_text(value: str) -> list[str]:
    return [tag.strip() for tag in re.sub(r"\s+", " ", value).split(",") if tag.strip()]


def split_caption(value: str) -> tuple[list[str], str]:
    """Python twin of CaptionFilterTools.splitCaptionText."""
    value = value.strip()
    if not value:
        return [], ""
    legacy = LEGACY_DESCRIPTION_RE.search(value)
    if legacy:
        return (
            split_tag_text(value[: legacy.start()]),
            re.sub(r"\s+", " ", value[legacy.end() :]).strip(),
        )
    value = re.sub(r"\s+", " ", value)
    boundary = INLINE_DESCRIPTION_RE.search(value)
    if boundary and "," in value[: boundary.start()]:
        return split_tag_text(value[: boundary.start()]), value[boundary.end() :].strip()
    return split_tag_text(value), ""


def join_caption(tags: list[str], description: str) -> str:
    """Python twin of CaptionFilterTools.joinCaptionText."""
    tags_text = ", ".join(tags)
    prose = re.sub(r"\s+", " ", description).strip()
    return f"{tags_text}. {prose}" if tags_text and prose else tags_text or prose


def partition_tags(
    tags: list[str], probability: float, rng: random.Random | None = None
) -> tuple[list[str], list[str]]:
    if _partition_tags_for_caption is not None:
        return _partition_tags_for_caption(tags, probability, rng)
    if not 0.0 <= probability <= 1.0:
        raise ValueError("Tag probability must be between 0.0 and 1.0.")
    if probability == 1.0:
        return list(tags), []
    source = rng or random
    selected: list[str] = []
    skipped: list[str] = []
    for tag in tags:
        (selected if probability and source.random() < probability else skipped).append(tag)
    return selected, skipped


def add_tags(
    existing: list[str],
    tags_to_add: list[str],
    *,
    randomize_placement: bool = False,
    important_placement: bool = False,
    rng: random.Random | None = None,
) -> tuple[list[str], bool]:
    if _append_tags_to_tag_list is not None:
        return _append_tags_to_tag_list(
            existing,
            tags_to_add,
            randomize_placement=randomize_placement,
            important_placement=important_placement,
            rng=rng,
        )
    result = list(existing)
    seen = {tag.lower() for tag in result}
    source = rng or random
    changed = False
    for tag in tags_to_add:
        if tag.lower() in seen:
            continue
        if randomize_placement:
            indexes = range(0, len(result) + 1)
            if important_placement:
                index = source.choices(indexes, weights=range(len(result) + 1, 0, -1), k=1)[0]
            else:
                index = source.randrange(0, len(result) + 1)
            result.insert(index, tag)
        else:
            result.append(tag)
        seen.add(tag.lower())
        changed = True
    return result, changed


@dataclass(frozen=True)
class CaptionItem:
    image_path: Path
    caption_path: Path
    relative_path: Path


def make_thumbnail(path: Path, size: int) -> bytes | None:
    """Return a JPEG thumbnail, or None when Pillow is unavailable."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    with Image.open(path) as source:
        source.draft("RGB", (size, size))
        image = ImageOps.exif_transpose(source)
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA", "P", "PA"}:
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, (32, 37, 45, 255))
            background.alpha_composite(image)
            image = background
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=86)
    return output.getvalue()


def natural_key(value: str) -> list[tuple[int, object]]:
    return [
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", value)
    ]


def scan_folder(root: Path) -> list[CaptionItem]:
    items = [
        CaptionItem(path, path.with_suffix(".txt"), path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
    ]
    return sorted(items, key=lambda item: natural_key(str(item.relative_path)))


class CaptionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path, page_path: Path) -> None:
        self.root = root.resolve()
        self.page_path = page_path
        self.lock_path = self.root / LOCK_FILENAME
        self._lock_owned = False
        self._acquire_instance_lock()
        self.items = scan_folder(self.root)
        self.items_lock = threading.RLock()
        self.save_lock = threading.Lock()
        self.state_path = self.root / STATE_FILENAME
        self.state_lock = threading.RLock()
        self.review_state, self.review_fingerprints = self._load_review_state()
        self.thumbnail_cache: OrderedDict[tuple[str, int, str], bytes] = OrderedDict()
        self.thumbnail_cache_bytes = 0
        self.thumbnail_lock = threading.Lock()
        self.thumbnail_workers = threading.BoundedSemaphore(max(2, (os.cpu_count() or 4) // 2))
        self._reconcile_review_state(self.items)
        try:
            super().__init__(address, CaptionHandler)
        except BaseException:
            self._release_instance_lock()
            raise

    def _acquire_instance_lock(self) -> None:
        if self.lock_path.exists():
            if reviewer_is_running(self.root):
                raise RuntimeError("Caption Reviewer is already running for this dataset.")
            self.lock_path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
                lock_file.write(str(os.getpid()))
            self._lock_owned = True
        except FileExistsError as exc:
            raise RuntimeError("Caption Reviewer is already starting for this dataset.") from exc

    def _release_instance_lock(self) -> None:
        if self._lock_owned:
            self.lock_path.unlink(missing_ok=True)
            self._lock_owned = False

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._release_instance_lock()

    def _load_review_state(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        try:
            return load_state(self.state_path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            print(f"Warning: could not read review state: {exc}", file=sys.stderr)
            return {}, {}

    @staticmethod
    def _fingerprint(item: CaptionItem) -> dict[str, str]:
        return {
            "caption": caption_hash(item.caption_path),
            "image": image_signature(item.image_path),
        }

    def _reconcile_review_state(self, items: list[CaptionItem]) -> None:
        with self.state_lock:
            by_key = {item.relative_path.as_posix(): item for item in items}
            changed = False
            for key in list(self.review_state):
                item = by_key.get(key)
                if item is None:
                    self.review_state.pop(key, None)
                    self.review_fingerprints.pop(key, None)
                    changed = True
                    continue
                current = self._fingerprint(item)
                previous = self.review_fingerprints.get(key)
                if previous and previous != current and self.review_state.get(key) != "E":
                    self.review_state[key] = "E"
                    changed = True
                if previous != current:
                    self.review_fingerprints[key] = current
                    changed = True
            for key in list(self.review_fingerprints):
                if key not in self.review_state:
                    self.review_fingerprints.pop(key, None)
                    changed = True
            if changed or self.state_path.exists():
                self._write_review_state_locked()

    def review_status(self, item: CaptionItem) -> str:
        key = item.relative_path.as_posix()
        with self.state_lock:
            return self.review_state.get(key, "")

    def mark_review_status(self, item: CaptionItem, status: str) -> str:
        """Record viewing or editing without viewing downgrading later states."""
        key = item.relative_path.as_posix()
        with self.state_lock:
            current = self.review_state.get(key, "")
            if status == "V":
                new_status = current or "V"
            elif status in {"E", "D"}:
                new_status = status
            else:
                raise ValueError("Review status must be V, E, or D")
            if new_status == current:
                fingerprint = self._fingerprint(item)
                if self.review_fingerprints.get(key) != fingerprint:
                    self.review_fingerprints[key] = fingerprint
                    self._write_review_state_locked()
                return current
            self.review_state[key] = new_status
            self.review_fingerprints[key] = self._fingerprint(item)
            self._write_review_state_locked()
            return new_status

    def set_review_status(self, item: CaptionItem, status: str) -> str:
        """Explicitly set a status, allowing the UI to cycle V, E, and D."""
        if status not in {"V", "E", "D"}:
            raise ValueError("Review status must be V, E, or D")
        key = item.relative_path.as_posix()
        with self.state_lock:
            if self.review_state.get(key) == status:
                fingerprint = self._fingerprint(item)
                if self.review_fingerprints.get(key) != fingerprint:
                    self.review_fingerprints[key] = fingerprint
                    self._write_review_state_locked()
                return status
            self.review_state[key] = status
            self.review_fingerprints[key] = self._fingerprint(item)
            self._write_review_state_locked()
            return status

    def _write_review_state_locked(self) -> None:
        """Write state atomically. Caller must hold state_lock."""
        try:
            save_state(self.state_path, self.review_state, self.review_fingerprints)
        except OSError as exc:
            print(f"Warning: could not save review state: {exc}", file=sys.stderr)

    def rescan(self) -> list[CaptionItem]:
        items = scan_folder(self.root)
        self._reconcile_review_state(items)
        with self.items_lock:
            self.items = items
        return items

    def thumbnail(self, item: CaptionItem, size: int) -> bytes | None:
        key = (item.relative_path.as_posix(), size, image_signature(item.image_path))
        with self.thumbnail_lock:
            data = self.thumbnail_cache.get(key)
            if data is not None:
                self.thumbnail_cache.move_to_end(key)
                return data
        with self.thumbnail_workers:
            data = make_thumbnail(item.image_path, size)
        if data is None:
            return None
        with self.thumbnail_lock:
            if key not in self.thumbnail_cache:
                self.thumbnail_cache[key] = data
                self.thumbnail_cache_bytes += len(data)
            while self.thumbnail_cache_bytes > THUMBNAIL_CACHE_BYTES and self.thumbnail_cache:
                _, evicted = self.thumbnail_cache.popitem(last=False)
                self.thumbnail_cache_bytes -= len(evicted)
        return data

    def items_by_path(self) -> dict[str, CaptionItem]:
        with self.items_lock:
            return {item.relative_path.as_posix(): item for item in self.items}

    def add_tags_to_items(
        self,
        paths: list[str],
        tags_to_add: list[str],
        *,
        probability: float = 1.0,
        randomize_placement: bool = False,
        important_placement: bool = False,
        rng: random.Random | None = None,
    ) -> dict[str, object]:
        """Add tags to many captions, keeping each description and any BOM."""
        if not 0.0 <= probability <= 1.0:
            raise ValueError("Tag probability must be between 0.0 and 1.0.")
        by_path = self.items_by_path()
        changed: list[str] = []
        unchanged: list[str] = []
        skipped: list[str] = []
        missing: list[str] = []
        failed: list[dict[str, str]] = []
        for path in dict.fromkeys(paths):
            item = by_path.get(path)
            if item is None or not item.image_path.is_file():
                missing.append(path)
                continue
            selected, _ = partition_tags(tags_to_add, probability, rng)
            if not selected:
                skipped.append(path)
                continue
            try:
                with self.save_lock:
                    try:
                        raw = item.caption_path.read_bytes()
                    except FileNotFoundError:
                        raw = b""
                    had_bom = raw.startswith(b"\xef\xbb\xbf")
                    tags, description = split_caption(raw.decode("utf-8-sig"))
                    new_tags, was_changed = add_tags(
                        tags,
                        selected,
                        randomize_placement=randomize_placement,
                        important_placement=important_placement,
                        rng=rng,
                    )
                    if not was_changed:
                        unchanged.append(path)
                        continue
                    data = join_caption(new_tags, description).encode("utf-8")
                    if had_bom:
                        data = b"\xef\xbb\xbf" + data
                    temporary = item.caption_path.with_name(
                        item.caption_path.name + ".caption-reviewer.tmp"
                    )
                    temporary.write_bytes(data)
                    os.replace(temporary, item.caption_path)
                self.mark_review_status(item, "E")
                changed.append(path)
            except (OSError, UnicodeError) as exc:
                failed.append({"path": path, "error": str(exc)})
        return {
            "changed": changed,
            "unchanged": unchanged,
            "skipped": skipped,
            "missing": missing,
            "failed": failed,
            "tagLogic": TAG_LOGIC,
        }

    def get_item(self, item_id: int) -> CaptionItem | None:
        with self.items_lock:
            if 0 <= item_id < len(self.items):
                return self.items[item_id]
        return None


class CaptionHandler(BaseHTTPRequestHandler):
    server: CaptionServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _item_id(self, query: dict[str, list[str]]) -> int | None:
        try:
            return int(query.get("id", [""])[0])
        except ValueError:
            return None

    def _item_list(self) -> list[dict[str, object]]:
        with self.server.items_lock:
            indexed_items = list(enumerate(self.server.items))

        def build_item(entry: tuple[int, CaptionItem]) -> dict[str, object]:
            index, item = entry
            try:
                caption_search = item.caption_path.read_bytes().decode("utf-8-sig")
                has_caption = True
            except FileNotFoundError:
                caption_search = ""
                has_caption = False
            except (OSError, UnicodeError):
                caption_search = ""
                has_caption = item.caption_path.exists()
            return {
                    "id": index,
                    "path": item.relative_path.as_posix(),
                    "name": item.relative_path.stem,
                    "hasCaption": has_caption,
                    "captionSearch": caption_search,
                    "reviewStatus": self.server.review_status(item),
                }

        workers = min(32, max(4, (os.cpu_count() or 4) * 4))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(build_item, indexed_items))

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            try:
                self._send_bytes(self.server.page_path.read_bytes(), "text/html; charset=utf-8")
            except OSError as exc:
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path in {"/caption_direction_tools.js", "/caption_filter_tools.js", "/caption_grid_tools.js"}:
            try:
                script_path = self.server.page_path.with_name(parsed.path.removeprefix("/"))
                self._send_bytes(
                    script_path.read_bytes(),
                    "text/javascript; charset=utf-8",
                    cache="no-cache",
                )
            except OSError as exc:
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/items":
            self._json({"root": str(self.server.root), "items": self._item_list()})
            return
        item_id = self._item_id(query)
        item = self.server.get_item(item_id) if item_id is not None else None
        if item is None:
            self._json({"error": "Unknown image id"}, HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/caption":
            try:
                if item.caption_path.exists():
                    raw = item.caption_path.read_bytes()
                    caption = raw.decode("utf-8-sig")
                    caption_version = hashlib.sha256(raw).hexdigest()
                else:
                    caption = ""
                    caption_version = "missing"
                review_status = self.server.mark_review_status(item, "V")
                self._json(
                    {
                        "caption": caption,
                        "hasCaption": item.caption_path.exists(),
                        "captionVersion": caption_version,
                        "reviewStatus": review_status,
                    }
                )
            except (OSError, UnicodeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/thumb":
            try:
                requested = int(query.get("size", ["256"])[0])
            except ValueError:
                requested = 256
            size = next((value for value in THUMBNAIL_SIZES if value >= requested), THUMBNAIL_SIZES[-1])
            try:
                data = self.server.thumbnail(item, size)
            except (OSError, ValueError) as exc:
                # Unreadable or unsupported image: the page falls back to /api/image.
                self._json({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if data is None:
                self._json({"error": "Pillow is not installed"}, HTTPStatus.NOT_IMPLEMENTED)
            else:
                self._send_bytes(data, "image/jpeg", cache="private, max-age=60")
            return
        if parsed.path == "/api/image":
            try:
                if item.image_path.suffix.casefold() in {".tif", ".tiff"}:
                    from PIL import Image, ImageOps

                    with Image.open(item.image_path) as source:
                        converted = ImageOps.exif_transpose(source).convert("RGB")
                        output = io.BytesIO()
                        converted.save(output, format="PNG")
                    data = output.getvalue()
                    content_type = "image/png"
                else:
                    data = item.image_path.read_bytes()
                    content_type = mimetypes.guess_type(item.image_path.name)[0] or "application/octet-stream"
                self._send_bytes(data, content_type, cache="no-store")
            except (ImportError, OSError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _save_caption(self, query: dict[str, list[str]]) -> None:
        item_id = self._item_id(query)
        item = self.server.get_item(item_id) if item_id is not None else None
        if item is None:
            self._json({"error": "Unknown image id"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_CAPTION_BYTES:
            self._json({"error": "Invalid caption request size"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            caption = payload["caption"]
            expected_version = payload.get("captionVersion")
            if not isinstance(caption, str):
                raise TypeError("caption must be text")
            with self.server.save_lock:
                if not item.image_path.is_file():
                    self._json(
                        {"error": "The image was moved or deleted. Refresh before saving."},
                        HTTPStatus.CONFLICT,
                    )
                    return
                try:
                    current_raw = item.caption_path.read_bytes()
                    current_version = hashlib.sha256(current_raw).hexdigest()
                except FileNotFoundError:
                    current_raw = b""
                    current_version = "missing"
                if expected_version is not None and expected_version != current_version:
                    self._json(
                        {"error": "The caption changed outside the reviewer. Refresh to load it; your unsaved text was not overwritten."},
                        HTTPStatus.CONFLICT,
                    )
                    return
                had_bom = current_raw.startswith(b"\xef\xbb\xbf")
                data = caption.encode("utf-8")
                if had_bom:
                    data = b"\xef\xbb\xbf" + data
                temporary = item.caption_path.with_name(
                    item.caption_path.name + ".caption-reviewer.tmp"
                )
                temporary.write_bytes(data)
                os.replace(temporary, item.caption_path)
                new_version = hashlib.sha256(data).hexdigest()
            review_status = self.server.mark_review_status(item, "E")
            self._json(
                {"saved": True, "reviewStatus": review_status, "captionVersion": new_version}
            )
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _add_tags(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_ADD_TAGS_BYTES:
            self._json({"error": "Invalid request size"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            paths = payload["paths"]
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise TypeError("paths must be a list of image paths")
            tags = split_tag_text(str(payload["tags"]))
            if not tags:
                raise ValueError("Enter at least one tag")
            probability = float(payload.get("probability", 1.0))
            randomize = bool(payload.get("randomize", False))
            important = bool(payload.get("important", False)) and randomize
            result = self.server.add_tags_to_items(
                paths,
                tags,
                probability=probability,
                randomize_placement=randomize,
                important_placement=important,
            )
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._json({**result, "items": self._item_list()})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/api/caption":
            self._save_caption(parse_qs(parsed.query))
        else:
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/api/caption":
            self._save_caption(parse_qs(parsed.query))
        elif parsed.path == "/api/status":
            query = parse_qs(parsed.query)
            item_id = self._item_id(query)
            item = self.server.get_item(item_id) if item_id is not None else None
            status = query.get("status", [""])[0]
            if item is None:
                self._json({"error": "Unknown image id"}, HTTPStatus.NOT_FOUND)
            elif status not in {"V", "E", "D"}:
                self._json({"error": "Status must be V, E, or D"}, HTTPStatus.BAD_REQUEST)
            else:
                self._json({"reviewStatus": self.server.set_review_status(item, status)})
        elif parsed.path == "/api/add-tags":
            self._add_tags()
        elif parsed.path == "/api/rescan":
            self.server.rescan()
            self._json({"root": str(self.server.root), "items": self._item_list()})
        else:
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review image captions in a browser.")
    parser.add_argument("folder", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--port", type=int, default=0, help="Local port (default: choose a free port)")
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # A quoted Windows batch argument ending in a backslash can occasionally
    # retain its closing quote. The launcher avoids that form, and this cleanup
    # also makes direct/custom launchers safe.
    root = Path(str(args.folder).strip().strip('"')).expanduser().resolve()
    page = Path(__file__).with_name("caption_reviewer.html")
    if not root.is_dir():
        raise SystemExit(f"Dataset folder does not exist: {root}")
    if not page.is_file():
        raise SystemExit(f"App page is missing: {page}")
    server = CaptionServer(("127.0.0.1", args.port), root, page)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"Caption Reviewer: {len(server.items):,} images in {root}")
    print(f"Open {url}")
    print("Keep this window open while reviewing. Press Ctrl+C here to stop.")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCaption Reviewer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
