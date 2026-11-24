#!/usr/bin/env python3
"""
Unit tests for upload protection and case-insensitive core file validation (Issue #18).
Ensures core node files and ignored system files cannot be overwritten via upload.
"""

import os
import sys
import json
import base64
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import HTTPServer

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import server
from server import GraphRequestHandler, find_available_port, CORE_NODE_FILES, IGNORED_SYSTEM_FILES, PROTECTED_NODE_FILES_LOWER


class TestUploadProtection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="kg_test_upload_")
        cls.test_graph_dir = Path(cls.test_dir) / "graph"
        cls.test_graph_dir.mkdir(parents=True, exist_ok=True)

        cls.orig_graph_dir = server.GRAPH_DIR
        server.GRAPH_DIR = cls.test_graph_dir

        cls.port = find_available_port(9300)
        cls.httpd = HTTPServer(("127.0.0.1", cls.port), GraphRequestHandler)
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        except Exception:
            pass
        server.GRAPH_DIR = cls.orig_graph_dir
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        for p in self.test_graph_dir.iterdir():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()

        # Create sample station
        self.station_dir = self.test_graph_dir / "math"
        self.station_dir.mkdir()
        (self.station_dir / "info.md").write_text("# Math Station\n\n**Status:** in-progress", encoding="utf-8")
        (self.station_dir / "notes.md").write_text("# Math Notes Content", encoding="utf-8")

        # Create shortcut pointing to math
        self.shortcut_dir = self.test_graph_dir / "math-link"
        self.shortcut_dir.mkdir()
        (self.shortcut_dir / "shortcut.json").write_text(json.dumps({"target": "math"}), encoding="utf-8")

    def _post_json(self, endpoint: str, payload: dict) -> tuple[int, dict]:
        url = f"{self.base_url}{endpoint}"
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.getcode()
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {"raw": str(e)}
            return status, body

    def _encode_content(self, text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    def test_protected_files_lower_set_contains_all_core_and_ignored(self):
        """Verify PROTECTED_NODE_FILES_LOWER set comprehensively folds all core and system filenames."""
        for f in CORE_NODE_FILES:
            self.assertIn(f.lower(), PROTECTED_NODE_FILES_LOWER)
        for f in IGNORED_SYSTEM_FILES:
            self.assertIn(f.lower(), PROTECTED_NODE_FILES_LOWER)
        self.assertIn("info.md", PROTECTED_NODE_FILES_LOWER)
        self.assertIn("notes.md", PROTECTED_NODE_FILES_LOWER)
        self.assertIn("shortcut.json", PROTECTED_NODE_FILES_LOWER)
        self.assertIn(".ds_store", PROTECTED_NODE_FILES_LOWER)
        self.assertIn("agents.md", PROTECTED_NODE_FILES_LOWER)

    def test_reject_exact_case_core_files(self):
        """Uploading exact case core node files is rejected with HTTP 400."""
        for core_name in ["info.md", "notes.md", "shortcut.json"]:
            status, res = self._post_json("/api/upload-extra-file", {
                "path": "math",
                "filename": core_name,
                "content": self._encode_content("malicious payload")
            })
            self.assertEqual(status, 400, f"Expected 400 for exact core file: {core_name}")
            self.assertEqual(res.get("error"), "Invalid filename or cannot overwrite core node files")

    def test_reject_case_variants_of_core_files(self):
        """Uploading case-insensitive variants of core node files is strictly rejected with HTTP 400."""
        variants = [
            "INFO.MD",
            "Info.md",
            "InFo.Md",
            "NOTES.MD",
            "Notes.md",
            "NoTeS.mD",
            "SHORTCUT.JSON",
            "shortcut.JSON",
            "ShortCut.Json"
        ]
        for variant in variants:
            status, res = self._post_json("/api/upload-extra-file", {
                "path": "math",
                "filename": variant,
                "content": self._encode_content("malicious payload")
            })
            self.assertEqual(status, 400, f"Expected 400 for case variant: {variant}")
            self.assertEqual(res.get("error"), "Invalid filename or cannot overwrite core node files")

        # Verify on-disk files were not modified or overwritten
        self.assertIn("# Math Station", (self.station_dir / "info.md").read_text(encoding="utf-8"))
        self.assertIn("# Math Notes Content", (self.station_dir / "notes.md").read_text(encoding="utf-8"))

    def test_reject_ignored_and_system_files(self):
        """Uploading system or agent files (and case variants) is rejected with HTTP 400."""
        system_files = [
            ".DS_Store",
            ".ds_store",
            "desktop.ini",
            "DESKTOP.INI",
            "Thumbs.db",
            "thumbs.db",
            "AGENTS.md",
            "agents.md",
            "Agents.MD",
            "agent.md",
            "AGENT.MD"
        ]
        for sys_file in system_files:
            status, res = self._post_json("/api/upload-extra-file", {
                "path": "math",
                "filename": sys_file,
                "content": self._encode_content("malicious payload")
            })
            self.assertEqual(status, 400, f"Expected 400 for system file: {sys_file}")
            self.assertEqual(res.get("error"), "Invalid filename or cannot overwrite core node files")

    def test_reject_empty_or_whitespace_filename(self):
        """Uploading with empty, whitespace, or invalid filename is rejected with HTTP 400."""
        for bad_name in ["", "   ", " \t "]:
            status, res = self._post_json("/api/upload-extra-file", {
                "path": "math",
                "filename": bad_name,
                "content": self._encode_content("data")
            })
            self.assertEqual(status, 400)
            self.assertIn("filename is required", res.get("error", ""))

        # Dot / path traversal that yields empty name
        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "..",
            "content": self._encode_content("data")
        })
        self.assertEqual(status, 400)
        self.assertEqual(res.get("error"), "Invalid filename or cannot overwrite core node files")

    def test_upload_valid_extra_file_succeeds(self):
        """Uploading valid extra files succeeds with HTTP 200 and writes file to disk."""
        content = "PDF Header Data %PDF-1.4"
        b64_content = f"data:application/pdf;base64,{self._encode_content(content)}"

        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "cheatsheet.pdf",
            "content": b64_content
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        self.assertEqual(res["file"]["label"], "cheatsheet.pdf")
        self.assertEqual(res["file"]["ext"], "pdf")
        self.assertEqual(res["file"]["size"], len(content))

        # Check file exists on disk
        target = self.station_dir / "cheatsheet.pdf"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_upload_alternative_endpoint_alias(self):
        """Uploading to /api/upload-resource alias endpoint functions identically."""
        status, res = self._post_json("/api/upload-resource", {
            "path": "math",
            "filename": "formula.svg",
            "content": self._encode_content("<svg></svg>")
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        self.assertTrue((self.station_dir / "formula.svg").exists())

    def test_overwrite_exact_same_filename_succeeds(self):
        """Uploading a file with exact same filename overwrites existing file content predictably."""
        initial_content = "version 1"
        self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "notes_supplement.txt",
            "content": self._encode_content(initial_content)
        })

        updated_content = "version 2 updated"
        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "notes_supplement.txt",
            "content": self._encode_content(updated_content)
        })
        self.assertEqual(status, 200)
        self.assertEqual((self.station_dir / "notes_supplement.txt").read_text(encoding="utf-8"), updated_content)

    def test_reject_case_collision_with_existing_file(self):
        """Uploading a file whose case collides with an existing file of different case is rejected with HTTP 400."""
        # Create cheatsheet.pdf
        (self.station_dir / "cheatsheet.pdf").write_text("original content", encoding="utf-8")

        # Attempt to upload CheatSheet.pdf (case mismatch collision)
        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "CheatSheet.pdf",
            "content": self._encode_content("new collision content")
        })
        self.assertEqual(status, 400)
        self.assertIn("collides with existing file", res.get("error", ""))
        self.assertIn("cheatsheet.pdf", res.get("error", ""))

        # Verify original file content was not mutated
        self.assertEqual((self.station_dir / "cheatsheet.pdf").read_text(encoding="utf-8"), "original content")

    def test_reject_collision_with_existing_directory(self):
        """Uploading a file whose name matches an existing child directory is rejected with HTTP 400."""
        child_dir = self.station_dir / "geometry"
        child_dir.mkdir()

        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math",
            "filename": "geometry",
            "content": self._encode_content("collision content")
        })
        self.assertEqual(status, 400)
        self.assertIn("already exists", res.get("error", ""))

    def test_upload_via_shortcut_station_resolves_and_guards(self):
        """Uploading through a shortcut station safely resolves to canonical station and enforces protection."""
        # Valid upload through math-link shortcut
        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math-link",
            "filename": "diagram.png",
            "content": self._encode_content("pngbytes")
        })
        self.assertEqual(status, 200)
        self.assertTrue((self.station_dir / "diagram.png").exists())

        # Blocked variant upload through shortcut
        status, res = self._post_json("/api/upload-extra-file", {
            "path": "math-link",
            "filename": "INFO.MD",
            "content": self._encode_content("malicious payload")
        })
        self.assertEqual(status, 400)
        self.assertEqual(res.get("error"), "Invalid filename or cannot overwrite core node files")


if __name__ == "__main__":
    unittest.main()
