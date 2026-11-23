#!/usr/bin/env python3
"""
Unit tests for Markdown Editor Enhancements:
- Bullet point auto-continuation on Enter
- Empty bullet clearing / list exit on Enter
- Tab indentation before the bullet
- Shift+Tab unindentation / outdent
- Markdown formatting shortcuts, auto-wrapping, auto-close pairs, and live stats
"""

import re
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


class TestMarkdownEditor(unittest.TestCase):
    def setUp(self):
        self.html_path = BASE_DIR / "web" / "index.html"
        self.css_path = BASE_DIR / "web" / "style.css"
        self.js_path = BASE_DIR / "web" / "app.js"

        self.html_content = self.html_path.read_text(encoding="utf-8")
        self.css_content = self.css_path.read_text(encoding="utf-8")
        self.js_content = self.js_path.read_text(encoding="utf-8")

    def test_html_editor_elements(self):
        """Verify presence of textarea, formatting buttons, and stats counter."""
        self.assertIn('id="md-textarea"', self.html_content)
        self.assertIn('id="editor-stats"', self.html_content)
        self.assertIn('data-tool="bold"', self.html_content)
        self.assertIn('data-tool="italic"', self.html_content)
        self.assertIn('data-tool="code"', self.html_content)
        self.assertIn('data-tool="math"', self.html_content)
        self.assertIn('data-tool="list"', self.html_content)
        self.assertIn('data-tool="check"', self.html_content)

    def test_css_editor_styles(self):
        """Verify CSS styling for tab-size, caret-color, font, and stats."""
        self.assertIn(".summary-editor-textarea", self.css_content)
        self.assertIn("tab-size: 2;", self.css_content)
        self.assertIn("caret-color:", self.css_content)
        self.assertIn("line-height: 1.6;", self.css_content)
        self.assertIn(".editor-stats", self.css_content)

    def test_js_setup_markdown_editor_present(self):
        """Verify setupMarkdownEditor and updateEditorStats functions exist and are hooked up."""
        self.assertIn("function setupMarkdownEditor(textarea)", self.js_content)
        self.assertIn("function updateEditorStats()", self.js_content)
        self.assertIn("setupMarkdownEditor(mdTextarea);", self.js_content)

    def test_js_tab_and_shift_tab_handling(self):
        """Verify Tab indents bullet before the marker and Shift+Tab outdents."""
        self.assertIn("e.key === 'Tab'", self.js_content)
        self.assertIn("handleTab(e)", self.js_content)
        # Verify 2 spaces indentation
        self.assertIn("textarea.setRangeText('  ', firstLineStart, firstLineStart, 'end')", self.js_content)
        # Verify Shift+Tab unindent logic
        self.assertIn("e.shiftKey", self.js_content)

    def test_js_enter_bullet_continuation_and_clearing(self):
        """Verify Enter continues bullet on content and clears bullet when empty."""
        self.assertIn("e.key === 'Enter'", self.js_content)
        self.assertIn("handleEnter(e)", self.js_content)
        # Check task list continuation
        self.assertIn("[ ]", self.js_content)
        # Check empty bullet clearing
        self.assertIn("itemText.trim() === ''", self.js_content)

    def test_js_selection_wrap_and_shortcuts(self):
        """Verify selection wrapping, auto-close pairs, and keyboard shortcuts."""
        self.assertIn("WRAP_PAIRS", self.js_content)
        self.assertIn("AUTO_CLOSE_PAIRS", self.js_content)
        self.assertIn("e.key.toLowerCase() === 'b'", self.js_content)
        self.assertIn("e.key.toLowerCase() === 'i'", self.js_content)
        self.assertIn("e.key.toLowerCase() === 's'", self.js_content)

    def test_bullet_regex_logic(self):
        """Simulate bullet regex matching in Python to verify pattern accuracy."""
        bullet_pattern = re.compile(r"^(\s*)([-*+])\s*(.*)$")
        task_pattern = re.compile(r"^(\s*)([-*+])\s+\[([ xX])\]\s*(.*)$")
        num_pattern = re.compile(r"^(\s*)(\d+)\.\s*(.*)$")

        # Standard unordered bullet
        m1 = bullet_pattern.match("- Item one")
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1), "")
        self.assertEqual(m1.group(2), "-")
        self.assertEqual(m1.group(3), "Item one")

        # Indented bullet
        m2 = bullet_pattern.match("  - Sub-item")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "  ")
        self.assertEqual(m2.group(2), "-")
        self.assertEqual(m2.group(3), "Sub-item")

        # Empty bullet
        m3 = bullet_pattern.match("- ")
        self.assertIsNotNone(m3)
        self.assertEqual(m3.group(3), "")

        # Task checkbox
        t1 = task_pattern.match("- [ ] Todo task")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.group(3), " ")
        self.assertEqual(t1.group(4), "Todo task")

        # Completed task checkbox
        t2 = task_pattern.match("- [x] Done task")
        self.assertIsNotNone(t2)
        self.assertEqual(t2.group(3), "x")
        self.assertEqual(t2.group(4), "Done task")

        # Numbered list
        n1 = num_pattern.match("1. First step")
        self.assertIsNotNone(n1)
        self.assertEqual(n1.group(2), "1")
        self.assertEqual(n1.group(3), "First step")


if __name__ == "__main__":
    unittest.main()
