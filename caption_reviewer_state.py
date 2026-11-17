"""Shared persistence helpers for Caption Reviewer and dataset mutation tools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


STATE_FILENAME = ".caption-reviewer-state.json"
LOCK_FILENAME = ".caption-reviewer.lock"
STATE_VERSION = 3
VALID_STATUSES = {"V", "E", "D"}
STATUS_PRIORITY = {"": 0, "V": 1, "E": 2, "D": 3}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def caption_hash(path: Path) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    return hashlib.sha256(data).hexdigest()


def image_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def load_state(path: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("version", 1)
    raw_files = payload.get("files", {})
    raw_fingerprints = payload.get("fingerprints", {})
    if not isinstance(raw_files, dict) or not isinstance(raw_fingerprints, dict):
        raise ValueError("Invalid Caption Reviewer state structure")
    files = {
        str(key): ("D" if value == "E" and version < 2 else value)
        for key, value in raw_files.items()
        if value in VALID_STATUSES
    }
    fingerprints = {
        str(key): {
            "caption": str(value.get("caption", "")),
            "image": str(value.get("image", "")),
        }
        for key, value in raw_fingerprints.items()
        if isinstance(value, dict)
    }
    return files, fingerprints


def save_state(
    path: Path,
    files: dict[str, str],
    fingerprints: dict[str, dict[str, str]],
) -> None:
    payload = {
        "version": STATE_VERSION,
        "files": dict(sorted(files.items())),
        "fingerprints": dict(sorted(fingerprints.items())),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def find_state_root(path: Path) -> Path | None:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / STATE_FILENAME).exists() or (candidate / "caption_reviewer.py").exists():
            return candidate
    return None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows os.kill(pid, 0) terminates the process. Query its handle
        # instead so checking an existing reviewer never stops that reviewer.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: process exists.
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reviewer_is_running(root: Path) -> bool:
    lock_path = root / LOCK_FILENAME
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    return _process_exists(pid)


def require_reviewer_stopped(root: Path) -> None:
    if reviewer_is_running(root):
        raise RuntimeError(
            "Caption Reviewer is running for this dataset. Close it before renaming, "
            "converting, or deleting dataset files."
        )


def relative_key(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def refresh_fingerprint(root: Path, image_path: Path) -> dict[str, str]:
    return {
        "caption": caption_hash(image_path.with_suffix(".txt")),
        "image": image_signature(image_path),
    }


def migrate_review_paths(anchor: Path, moves: Iterable[tuple[Path, Path]]) -> int:
    root = find_state_root(anchor)
    if root is None:
        return 0
    require_reviewer_stopped(root)
    state_path = root / STATE_FILENAME
    files, fingerprints = load_state(state_path)
    keyed_moves = [
        (old_key, new_key, new.resolve())
        for old, new in moves
        if (old_key := relative_key(root, old)) is not None
        and (new_key := relative_key(root, new)) is not None
        and old_key != new_key
    ]
    original_files = dict(files)
    original_fingerprints = dict(fingerprints)
    for old_key, _, _ in keyed_moves:
        files.pop(old_key, None)
        fingerprints.pop(old_key, None)
    migrated = 0
    for old_key, new_key, new_path in keyed_moves:
        old_status = original_files.get(old_key)
        if old_status:
            current = files.get(new_key, "")
            files[new_key] = max((current, old_status), key=lambda value: STATUS_PRIORITY.get(value, 0))
            migrated += 1
        if old_key in original_fingerprints or old_status:
            fingerprints[new_key] = refresh_fingerprint(root, new_path)
    if keyed_moves:
        save_state(state_path, files, fingerprints)
    return migrated


def remove_review_paths(anchor: Path, paths: Iterable[Path]) -> int:
    root = find_state_root(anchor)
    if root is None:
        return 0
    require_reviewer_stopped(root)
    state_path = root / STATE_FILENAME
    files, fingerprints = load_state(state_path)
    removed = 0
    for path in paths:
        key = relative_key(root, path)
        if key is None:
            continue
        if files.pop(key, None) is not None:
            removed += 1
        fingerprints.pop(key, None)
    if removed:
        save_state(state_path, files, fingerprints)
    return removed


def refresh_review_paths(anchor: Path, paths: Iterable[Path]) -> int:
    """Accept intentional file replacements while preserving their statuses."""
    root = find_state_root(anchor)
    if root is None:
        return 0
    require_reviewer_stopped(root)
    state_path = root / STATE_FILENAME
    files, fingerprints = load_state(state_path)
    refreshed = 0
    for path in paths:
        key = relative_key(root, path)
        if key is not None and key in files and path.is_file():
            fingerprints[key] = refresh_fingerprint(root, path)
            refreshed += 1
    if refreshed:
        save_state(state_path, files, fingerprints)
    return refreshed


def prune_missing_review_paths(anchor: Path) -> int:
    root = find_state_root(anchor)
    if root is None:
        return 0
    require_reviewer_stopped(root)
    state_path = root / STATE_FILENAME
    files, fingerprints = load_state(state_path)
    stale = [key for key in files if not (root / Path(key)).is_file()]
    for key in stale:
        files.pop(key, None)
        fingerprints.pop(key, None)
    if stale:
        save_state(state_path, files, fingerprints)
    return len(stale)
