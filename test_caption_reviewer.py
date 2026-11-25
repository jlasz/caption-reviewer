"""Exercise the standalone app with generated, temporary sample data only."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from caption_reviewer import CaptionServer, scan_folder
from caption_reviewer_state import (
    LOCK_FILENAME,
    STATE_FILENAME,
    _process_exists,
    load_state,
    reviewer_is_running,
)


class ReviewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="caption-reviewer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # A generated 1x1 PNG; no user images or captions are accessed.
        self.image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII="
        )
        for name in ("image10.png", "image2.png"):
            (self.root / name).write_bytes(self.image)
        (self.root / "image2.txt").write_text("landscape, sunset. A quiet sky.", encoding="utf-8")
        self.page = Path(__file__).with_name("caption_reviewer.html")
        self.server = CaptionServer(("127.0.0.1", 0), self.root, self.page)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)

    def request(self, path: str, method: str = "GET", payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.url + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with self.opener.open(request, timeout=5) as response:
            return response.headers, response.read()

    def api(self, path: str, method: str = "GET", payload: dict | None = None):
        return json.loads(self.request(path, method, payload)[1])

    def test_assets_and_natural_sort(self) -> None:
        self.assertEqual([item.relative_path.name for item in scan_folder(self.root)],
                         ["image2.png", "image10.png"])
        for path, expected in (("/", b"Caption Reviewer"),
                               ("/caption_filter_tools.js", b"CaptionFilterTools"),
                               ("/caption_direction_tools.js", b"CaptionDirectionTools")):
            self.assertIn(expected, self.request(path)[1])
        self.assertEqual(self.request("/api/image?id=0")[1], self.image)
        items = self.api("/api/items")["items"]
        self.assertEqual([item["hasCaption"] for item in items], [True, False])

    def test_save_conflict_and_progress(self) -> None:
        original = self.api("/api/caption?id=0")
        self.assertEqual(original["reviewStatus"], "V")
        updated = "landscape, clouds. A bright sky."
        result = self.api("/api/caption?id=0", "PUT", {
            "caption": updated, "captionVersion": original["captionVersion"],
        })
        self.assertEqual(result["reviewStatus"], "E")
        self.assertEqual((self.root / "image2.txt").read_text(encoding="utf-8"), updated)
        external = "landscape, moon. A night sky."
        (self.root / "image2.txt").write_text(external, encoding="utf-8")
        with self.assertRaises(HTTPError) as caught:
            self.api("/api/caption?id=0", "PUT", {
                "caption": updated, "captionVersion": result["captionVersion"],
            })
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        self.assertEqual((self.root / "image2.txt").read_text(encoding="utf-8"), external)
        self.api("/api/status?id=0&status=D", "POST")
        files, _ = load_state(self.root / STATE_FILENAME)
        self.assertEqual(files["image2.png"], "D")
        (self.root / "image2.txt").write_text("changed outside the app", encoding="utf-8")
        items = self.api("/api/rescan", "POST")["items"]
        self.assertEqual(items[0]["reviewStatus"], "E")

    def test_missing_caption_and_bom(self) -> None:
        original = self.api("/api/caption?id=1")
        self.assertEqual(original["captionVersion"], "missing")
        result = self.api("/api/caption?id=1", "POST", {
            "caption": "clouds, sky", "captionVersion": "missing",
        })
        self.assertTrue(result["saved"])
        self.assertEqual((self.root / "image10.txt").read_text(encoding="utf-8"), "clouds, sky")
        (self.root / "image2.txt").write_bytes(b"\xef\xbb\xbfclouds, sky")
        original = self.api("/api/caption?id=0")
        self.api("/api/caption?id=0", "PUT", {
            "caption": "clouds, sun", "captionVersion": original["captionVersion"],
        })
        self.assertEqual((self.root / "image2.txt").read_bytes(), b"\xef\xbb\xbfclouds, sun")

    def test_thumbnail_endpoint(self) -> None:
        self.assertIn(b"CaptionGridTools", self.request("/caption_grid_tools.js")[1])
        try:
            from PIL import Image
        except ImportError:
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/thumb?id=0&size=200")
            self.assertEqual(caught.exception.code, 501)
            caught.exception.close()
            return
        import io

        big = Image.new("RGBA", (900, 300), (255, 0, 0, 128))
        buffer = io.BytesIO()
        big.save(buffer, format="PNG")
        (self.root / "image2.png").write_bytes(buffer.getvalue())
        headers, data = self.request("/api/thumb?id=0&size=200")
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        with Image.open(io.BytesIO(data)) as thumb:
            self.assertEqual(thumb.size, (256, 85))
        self.assertEqual(self.request("/api/thumb?id=0&size=200")[1], data)  # Cached.
        (self.root / "image10.png").write_bytes(b"not an image")
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/thumb?id=1&size=200")
        self.assertEqual(caught.exception.code, 422)
        caught.exception.close()
        self.assertFalse(any(path.suffix == ".jpg" for path in self.root.rglob("*")))

    def test_add_tags_to_selected_images(self) -> None:
        (self.root / "image2.txt").write_bytes(b"\xef\xbb\xbflandscape, Sunset. A quiet sky. 2 birds fly.")
        self.api("/api/status?id=0&status=D", "POST")
        result = self.api("/api/add-tags", "POST", {
            "paths": ["image2.png", "image10.png", "image2.png", "gone.png"],
            "tags": " sunset ,  red sky,, ",
        })
        self.assertEqual(result["changed"], ["image2.png", "image10.png"])
        self.assertEqual(result["missing"], ["gone.png"])
        self.assertEqual(
            (self.root / "image2.txt").read_bytes(),
            b"\xef\xbb\xbflandscape, Sunset, red sky. A quiet sky. 2 birds fly.",
        )
        self.assertEqual((self.root / "image10.txt").read_text(encoding="utf-8"), "sunset, red sky")
        statuses = {item["path"]: item["reviewStatus"] for item in result["items"]}
        self.assertEqual(statuses, {"image2.png": "E", "image10.png": "E"})
        searchable = {item["path"]: item["captionSearch"] for item in result["items"]}
        self.assertIn("red sky", searchable["image10.png"])
        again = self.api("/api/add-tags", "POST", {"paths": ["image10.png"], "tags": "red sky"})
        self.assertEqual(again["unchanged"], ["image10.png"])
        skipped = self.api("/api/add-tags", "POST", {
            "paths": ["image10.png"], "tags": "night", "probability": 0,
        })
        self.assertEqual(skipped["skipped"], ["image10.png"])
        # Edits made through the grid are not mistaken for external changes.
        items = self.api("/api/rescan", "POST")["items"]
        self.assertEqual([item["reviewStatus"] for item in items], ["E", "E"])
        for payload in ({"paths": ["image10.png"], "tags": " , "},
                        {"paths": "image10.png", "tags": "x"},
                        {"paths": ["image10.png"], "tags": "x", "probability": 2}):
            with self.assertRaises(HTTPError) as caught:
                self.api("/api/add-tags", "POST", payload)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()

    def test_add_tags_random_placement_keeps_description(self) -> None:
        import random
        from caption_reviewer import add_tags, split_caption, join_caption

        tags, description = split_caption("a, b, c. Prose stays. Intact.")
        self.assertEqual((tags, description), (["a", "b", "c"], "Prose stays. Intact."))
        placed, changed = add_tags(tags, ["x", "B"], randomize_placement=True, rng=random.Random(3))
        self.assertTrue(changed)
        self.assertEqual(sorted(placed), ["a", "b", "c", "x"])
        self.assertTrue(join_caption(placed, description).endswith(". Prose stays. Intact."))
        self.assertEqual(split_caption("a, b\n\nLegacy prose."), (["a", "b"], "Legacy prose."))

    def test_duplicate_instance_is_rejected_without_stopping_process(self) -> None:
        self.assertTrue(_process_exists(os.getpid()))
        self.assertFalse(_process_exists(-1))
        self.assertTrue(reviewer_is_running(self.root))
        with self.assertRaisesRegex(RuntimeError, "already running"):
            CaptionServer(("127.0.0.1", 0), self.root, self.page)
        self.assertEqual(self.request("/api/image?id=0")[1], self.image)
        self.stop_server()
        self.server.server_close()
        self.assertFalse((self.root / LOCK_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
