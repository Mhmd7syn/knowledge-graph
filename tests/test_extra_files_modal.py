#!/usr/bin/env python3
"""Contract tests for Extra Files modal DOM IDs and null guards."""

import re
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _extract_js_function(js_source: str, func_name: str) -> str:
    """Extract a function block by balanced braces starting at `function <name>`."""
    marker = f"function {func_name}("
    start = js_source.find(marker)
    if start == -1:
        raise AssertionError(f"Function {func_name} not found in web/app.js")
    brace_start = js_source.find("{", start)
    depth = 0
    for idx in range(brace_start, len(js_source)):
        ch = js_source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js_source[start : idx + 1]
    raise AssertionError(f"Unbalanced braces in {func_name}")


class TestExtraFilesModal(unittest.TestCase):
    def setUp(self):
        self.html_content = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
        self.js_content = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
        self.fn_body = _extract_js_function(self.js_content, "openExtraFilesModal")

    def test_html_contains_expected_extra_files_modal_ids(self):
        """Verify web/index.html defines all expected Extra Files modal element IDs."""
        for elem_id in (
            "extra-files-modal-overlay",
            "extra-files-path",
            "extra-files-list",
        ):
            self.assertIn(f'id="{elem_id}"', self.html_content)

    def test_open_extra_files_modal_ids_match_html(self):
        """Every getElementById target in openExtraFilesModal must exist in web/index.html."""
        self.assertNotIn("extra-files-node-label", self.js_content)
        queried_ids = re.findall(
            r"document\.getElementById\(['\"]([^'\"]+)['\"]\)", self.fn_body
        )
        self.assertEqual(
            set(queried_ids),
            {"extra-files-modal-overlay", "extra-files-list", "extra-files-path"},
        )
        for elem_id in queried_ids:
            self.assertIn(
                f'id="{elem_id}"',
                self.html_content,
                f"Missing HTML element id={elem_id!r} queried in openExtraFilesModal",
            )

    def test_open_extra_files_modal_has_null_guards(self):
        """Verify null guards protect nodeTitleLabel, listContainer, and overlay."""
        self.assertRegex(self.fn_body, r"if\s*\(\s*nodeTitleLabel\s*\)\s*\{")
        self.assertRegex(self.fn_body, r"if\s*\(\s*listContainer\s*\)\s*\{")
        self.assertRegex(self.fn_body, r"if\s*\(\s*overlay\s*\)\s*\{")


if __name__ == "__main__":
    unittest.main()
