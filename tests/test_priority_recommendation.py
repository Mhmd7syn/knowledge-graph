#!/usr/bin/env python3
"""
Unit and integration tests for Priority-Weighted Node Recommendation Engine (Issue #12).
"""

import os
import re
import sys
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import HTTPServer

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import server
from server import GraphRequestHandler, find_available_port


class TestPriorityRecommendation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="kg_test_priority_")
        cls.test_graph_dir = Path(cls.test_dir) / "graph"
        cls.test_graph_dir.mkdir(parents=True, exist_ok=True)

        cls.orig_graph_dir = server.GRAPH_DIR
        server.GRAPH_DIR = cls.test_graph_dir

        cls.httpd = None
        cls.use_http = False
        try:
            cls.port = find_available_port(9260)
            cls.httpd = HTTPServer(("127.0.0.1", cls.port), GraphRequestHandler)
            cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
            cls.server_thread.start()
            cls.base_url = f"http://127.0.0.1:{cls.port}"
            cls.use_http = True
        except (PermissionError, OSError):
            cls.httpd = None
            cls.use_http = False

    def setUp(self):
        pass

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            try:
                cls.httpd.shutdown()
                cls.httpd.server_close()
            except Exception:
                pass
        server.GRAPH_DIR = cls.orig_graph_dir
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _invoke_handler(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        import io
        handler = GraphRequestHandler.__new__(GraphRequestHandler)
        handler.command = method
        handler.path = path
        handler.headers = {"Content-Type": "application/json"}
        body_bytes = json.dumps(payload).encode("utf-8") if payload else b""
        handler.rfile = io.BytesIO(body_bytes)
        handler.wfile = io.BytesIO()
        handler.headers["Content-Length"] = str(len(body_bytes))

        status_code = [200]
        def mock_send_response(code, message=None):
            status_code[0] = code
        handler.send_response = mock_send_response
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None

        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()

        raw_output = handler.wfile.getvalue().decode("utf-8")
        try:
            body = json.loads(raw_output) if raw_output.strip() else {}
        except Exception:
            body = {"raw": raw_output}
        return status_code[0], body

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        if getattr(self, "use_http", False):
            url = f"{self.base_url}{path}"
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.getcode(), json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8"))
            except urllib.error.URLError:
                self.use_http = False
        return self._invoke_handler("POST", path, payload)

    def _get(self, path: str) -> tuple[int, dict]:
        if getattr(self, "use_http", False):
            url = f"{self.base_url}{path}"
            try:
                with urllib.request.urlopen(url) as resp:
                    raw = resp.read().decode("utf-8")
                    return resp.getcode(), (json.loads(raw) if raw.strip() else {})
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                return e.code, (json.loads(raw) if raw.strip() else {})
            except urllib.error.URLError:
                self.use_http = False
        return self._invoke_handler("GET", path)

    def test_parse_info_md_priority_extraction(self):
        handler = GraphRequestHandler.__new__(GraphRequestHandler)

        # 1. Missing priority defaults to normal
        p1 = self.test_graph_dir / "station_default"
        p1.mkdir(parents=True, exist_ok=True)
        (p1 / "info.md").write_text("# Station Default\n\n**Status:** todo\n", encoding="utf-8")
        title, badge, status, priority = handler._parse_info_md(p1 / "info.md", "Station Default")
        self.assertEqual(priority, "normal")

        # 2. Explicit high priority
        p2 = self.test_graph_dir / "station_high"
        p2.mkdir(parents=True, exist_ok=True)
        (p2 / "info.md").write_text("# Station High\n\n**Status:** todo\n**Priority:** high\n", encoding="utf-8")
        _, _, _, priority = handler._parse_info_md(p2 / "info.md", "Station High")
        self.assertEqual(priority, "high")

        # 3. Explicit low priority with lowercase header
        p3 = self.test_graph_dir / "station_low"
        p3.mkdir(parents=True, exist_ok=True)
        (p3 / "info.md").write_text("# Station Low\n\nstatus: todo\npriority: low\n", encoding="utf-8")
        _, _, _, priority = handler._parse_info_md(p3 / "info.md", "Station Low")
        self.assertEqual(priority, "low")

        # 4. Invalid priority defaults to normal
        p4 = self.test_graph_dir / "station_invalid"
        p4.mkdir(parents=True, exist_ok=True)
        (p4 / "info.md").write_text("# Station Invalid\n\n**Priority:** super-urgent\n", encoding="utf-8")
        _, _, _, priority = handler._parse_info_md(p4 / "info.md", "Station Invalid")
        self.assertEqual(priority, "normal")

    def test_save_priority_endpoint_and_persistence(self):
        st = self.test_graph_dir / "track" / "node_a"
        st.mkdir(parents=True, exist_ok=True)
        (st / "info.md").write_text("# Node A\n\n**Status:** todo\n", encoding="utf-8")

        status, body = self._post("/api/save-priority", {"path": "track/node_a", "priority": "high"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertEqual(body["node"]["priority"], "high")

        # Verify on disk
        content = (st / "info.md").read_text(encoding="utf-8")
        self.assertIn("**Priority:** high", content)

    def test_save_info_with_priority_payload(self):
        st = self.test_graph_dir / "track" / "node_b"
        st.mkdir(parents=True, exist_ok=True)

        status, body = self._post("/api/save-info", {
            "path": "track/node_b",
            "content": "# Node B\n\n**Status:** todo\n",
            "priority": "low"
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["node"]["priority"], "low")

        content = (st / "info.md").read_text(encoding="utf-8")
        self.assertIn("**Priority:** low", content)

    def test_statelessness_no_side_effects(self):
        """Verify recommendation engine is 100% stateless with no side effects."""
        st = self.test_graph_dir / "stateless_test"
        st.mkdir(parents=True, exist_ok=True)
        (st / "info.md").write_text("# Stateless Test\n\n**Status:** todo\n**Priority:** high\n", encoding="utf-8")

        status, data = self._get(f"/api/next-node?path={urllib.parse.quote('stateless_test')}")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("found"))

    def test_priority_weighted_effective_progress_order(self):
        """
        Verify:
        (completed / (total * weight), -weight, title.lower())
        When 3 uncompleted stations start with 0 progress:
        - High (weight 2.0) is picked first due to tie breaker -2.0.
        - After High is completed: High progress is 1/(1*2) = 0.5.
          Normal (0.0) and Low (0.0) are lower -> Normal is picked (weight 1.0 > 0.5).
        - After Normal is completed: Normal progress is 1/(1*1) = 1.0.
          Low (0.0) is lowest -> Low is picked!
        """
        sandbox = self.test_graph_dir / "prio_order_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Prio Order Test\n\n**Status:** completed\n", encoding="utf-8")

        for name, prio in [("station_h", "high"), ("station_n", "normal"), ("station_l", "low")]:
            d = sandbox / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "info.md").write_text(f"# {name}\n\n**Status:** todo\n**Priority:** {prio}\n", encoding="utf-8")

        p_path = urllib.parse.quote("prio_order_test")

        # 1. High station is recommended first
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d1["found"])
        self.assertEqual(d1["node"]["id"], "station_h")
        self.assertEqual(d1["node"]["priority"], "high")

        # Complete High station
        self._post("/api/save-status", {"path": "prio_order_test/station_h", "status": "completed"})

        # 2. Normal station is recommended next
        _, d2 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d2["found"])
        self.assertEqual(d2["node"]["id"], "station_n")
        self.assertEqual(d2["node"]["priority"], "normal")

        # Complete Normal station
        self._post("/api/save-status", {"path": "prio_order_test/station_n", "status": "completed"})

        # 3. Low station is recommended next
        _, d3 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d3["found"])
        self.assertEqual(d3["node"]["id"], "station_l")
        self.assertEqual(d3["node"]["priority"], "low")

        # Complete Low station -> all completed!
        self._post("/api/save-status", {"path": "prio_order_test/station_l", "status": "completed"})
        _, d4 = self._get(f"/api/next-node?path={p_path}")
        self.assertFalse(d4["found"])
        self.assertTrue(d4["allCompleted"])

    def test_completion_percentage_catch_up_without_starvation(self):
        """
        Verify:
        Topic A (weight 2.0): 10/20 completed (50%) -> progress = 0.50 / 2.0 = 0.25.
        Topic B (weight 1.0): 5/10 completed (50%) -> progress = 0.50 / 1.0 = 0.50.
        New Topic C (weight 1.0): 0/10 completed (0%) -> progress = 0.0 / 1.0 = 0.0.

        C starts at 0.0 -> recommended for station 1 (reaches 10% = 0.10) and station 2 (reaches 20% = 0.20).
        Station 3 reaches 30% = 0.30 > 0.25.
        Now Topic A (0.25) is lower than Topic C (0.30)!
        Recommendation smoothly alternates back to Topic A without starving it for dozens of stations.
        """
        sandbox = self.test_graph_dir / "catch_up_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Catch Up Test\n\n**Status:** completed\n", encoding="utf-8")

        # Topic A: High (weight 2.0), parent completed, 10 completed children, 10 todo children (total 21 stations)
        dir_a = sandbox / "topic_a"
        dir_a.mkdir(parents=True, exist_ok=True)
        (dir_a / "info.md").write_text("# Topic A\n\n**Status:** completed\n**Priority:** high\n", encoding="utf-8")
        for i in range(1, 11):
            ca = dir_a / f"a_comp_{i:02d}"
            ca.mkdir(parents=True, exist_ok=True)
            (ca / "info.md").write_text(f"# A Comp {i:02d}\n\n**Status:** completed\n", encoding="utf-8")
        for i in range(1, 11):
            ca = dir_a / f"a_todo_{i:02d}"
            ca.mkdir(parents=True, exist_ok=True)
            (ca / "info.md").write_text(f"# A Todo {i:02d}\n\n**Status:** todo\n", encoding="utf-8")

        # Topic B: Normal (weight 1.0), parent completed, 5 completed, 5 todo (total 11 stations)
        dir_b = sandbox / "topic_b"
        dir_b.mkdir(parents=True, exist_ok=True)
        (dir_b / "info.md").write_text("# Topic B\n\n**Status:** completed\n**Priority:** normal\n", encoding="utf-8")
        for i in range(1, 6):
            cb = dir_b / f"b_comp_{i:02d}"
            cb.mkdir(parents=True, exist_ok=True)
            (cb / "info.md").write_text(f"# B Comp {i:02d}\n\n**Status:** completed\n", encoding="utf-8")
        for i in range(1, 6):
            cb = dir_b / f"b_todo_{i:02d}"
            cb.mkdir(parents=True, exist_ok=True)
            (cb / "info.md").write_text(f"# B Todo {i:02d}\n\n**Status:** todo\n", encoding="utf-8")

        # Topic C: Normal (weight 1.0), parent completed, 0 completed, 10 todo (total 11 stations)
        dir_c = sandbox / "topic_c"
        dir_c.mkdir(parents=True, exist_ok=True)
        (dir_c / "info.md").write_text("# Topic C\n\n**Status:** completed\n**Priority:** normal\n", encoding="utf-8")
        for i in range(1, 11):
            cc = dir_c / f"c_todo_{i:02d}"
            cc.mkdir(parents=True, exist_ok=True)
            (cc / "info.md").write_text(f"# C Todo {i:02d}\n\n**Status:** todo\n", encoding="utf-8")

        p_path = urllib.parse.quote("catch_up_test")

        # 1. Topic C has progress = 1 / (11 * 1.0) = 0.091 (since dir_c is completed, comp=1, tot=11)
        # Topic A progress = 11 / (21 * 2.0) = 0.2619
        # Topic B progress = 6 / (11 * 1.0) = 0.545
        # C is lowest -> recommends c_todo_01
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertEqual(d1["node"]["id"], "c_todo_01")

        # Complete c_todo_01 -> Topic C comp = 2 / 11 = 0.1818 < 0.2619
        self._post("/api/save-status", {"path": "catch_up_test/topic_c/c_todo_01", "status": "completed"})
        _, d2 = self._get(f"/api/next-node?path={p_path}")
        self.assertEqual(d2["node"]["id"], "c_todo_02")

        # Complete c_todo_02 -> Topic C comp = 3 / 11 = 0.2727 > 0.2619 (Topic A)!
        self._post("/api/save-status", {"path": "catch_up_test/topic_c/c_todo_02", "status": "completed"})

        # 3. Topic A (0.2619) is now LOWER than Topic C (0.2727)!
        # Engine switches back to Topic A!
        _, d3 = self._get(f"/api/next-node?path={p_path}")
        self.assertEqual(d3["node"]["id"], "a_todo_01")

    def test_recursive_descent_into_completed_parent(self):
        """When a top-ranked category is completed, traversal recurses to find the uncompleted child."""
        sandbox = self.test_graph_dir / "descent_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Descent Test\n\n**Status:** completed\n", encoding="utf-8")

        cat = sandbox / "completed_category"
        cat.mkdir(parents=True, exist_ok=True)
        (cat / "info.md").write_text("# Completed Category\n\n**Status:** completed\n**Priority:** high\n", encoding="utf-8")

        child = cat / "deep_child"
        child.mkdir(parents=True, exist_ok=True)
        (child / "info.md").write_text("# Deep Child\n\n**Status:** todo\n", encoding="utf-8")

        p_path = urllib.parse.quote("descent_test")
        _, data = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(data["found"])
        self.assertEqual(data["node"]["id"], "deep_child")
        self.assertEqual(data["node"]["relPath"], "descent_test/completed_category/deep_child")

    def test_uncompleted_parent_station_recommended_before_children(self):
        """An uncompleted parent station must be completed before descending to its children."""
        sandbox = self.test_graph_dir / "parent_first_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Parent First Test\n\n**Status:** completed\n", encoding="utf-8")

        parent_st = sandbox / "launchpad_station"
        parent_st.mkdir(parents=True, exist_ok=True)
        (parent_st / "info.md").write_text("# Launchpad Station\n\n**Status:** todo\n**Priority:** high\n", encoding="utf-8")

        child_st = parent_st / "sub_topic"
        child_st.mkdir(parents=True, exist_ok=True)
        (child_st / "info.md").write_text("# Sub Topic\n\n**Status:** todo\n", encoding="utf-8")

        p_path = urllib.parse.quote("parent_first_test")

        # 1. Launchpad station is uncompleted -> returned as next node
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertEqual(d1["node"]["id"], "launchpad_station")

        # 2. Once launchpad station is completed -> descends into sub_topic
        self._post("/api/save-status", {"path": "parent_first_test/launchpad_station", "status": "completed"})
        _, d2 = self._get(f"/api/next-node?path={p_path}")
        self.assertEqual(d2["node"]["id"], "sub_topic")

    def test_only_status_completed_counts(self):
        """Only status == 'completed' counts as completed. needs-review, in-progress, and todo are pending work."""
        sandbox = self.test_graph_dir / "status_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Status Test\n\n**Status:** completed\n", encoding="utf-8")

        # s_rev has needs-review status (e.g. decayed)
        s_rev = sandbox / "s_rev"
        s_rev.mkdir(parents=True, exist_ok=True)
        (s_rev / "info.md").write_text("# S Rev\n\n**Status:** needs-review\n**Priority:** high\n", encoding="utf-8")

        # s_prog has in-progress status
        s_prog = sandbox / "s_prog"
        s_prog.mkdir(parents=True, exist_ok=True)
        (s_prog / "info.md").write_text("# S Prog\n\n**Status:** in-progress\n**Priority:** low\n", encoding="utf-8")

        p_path = urllib.parse.quote("status_test")

        # High priority with needs-review is recommended (pending work)
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d1["found"])
        self.assertEqual(d1["node"]["id"], "s_rev")
        self.assertEqual(d1["node"]["status"], "needs-review")

        # Mark s_rev completed
        self._post("/api/save-status", {"path": "status_test/s_rev", "status": "completed"})

        # s_prog is recommended next
        _, d2 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d2["found"])
        self.assertEqual(d2["node"]["id"], "s_prog")
        self.assertEqual(d2["node"]["status"], "in-progress")

    def test_alphabetical_tie_breaking(self):
        """When progress and weight are equal, ties are broken alphabetically by title."""
        sandbox = self.test_graph_dir / "tie_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# Tie Test\n\n**Status:** completed\n", encoding="utf-8")

        z_dir = sandbox / "z_node"
        z_dir.mkdir(parents=True, exist_ok=True)
        (z_dir / "info.md").write_text("# Zebra Station\n\n**Status:** todo\n**Priority:** normal\n", encoding="utf-8")

        a_dir = sandbox / "a_node"
        a_dir.mkdir(parents=True, exist_ok=True)
        (a_dir / "info.md").write_text("# Apple Station\n\n**Status:** todo\n**Priority:** normal\n", encoding="utf-8")

        p_path = urllib.parse.quote("tie_test")
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertTrue(d1["found"])
        self.assertEqual(d1["node"]["title"], "Apple Station")

    def test_all_completed_when_entire_graph_done(self):
        """When all nodes are completed, returns allCompleted: True and found: False."""
        sandbox = self.test_graph_dir / "all_done_test"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "info.md").write_text("# All Done Test\n\n**Status:** completed\n", encoding="utf-8")

        s1 = sandbox / "station_1"
        s1.mkdir(parents=True, exist_ok=True)
        (s1 / "info.md").write_text("# Station 1\n\n**Status:** completed\n", encoding="utf-8")

        p_path = urllib.parse.quote("all_done_test")
        _, d1 = self._get(f"/api/next-node?path={p_path}")
        self.assertFalse(d1["found"])
        self.assertTrue(d1["allCompleted"])

    def test_ui_markup_and_css_priority_support(self):
        html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
        css = (BASE_DIR / "web" / "style.css").read_text(encoding="utf-8")
        js = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="next-node-pill"', html)
        self.assertIn('id="next-node-title"', html)
        self.assertIn('id="md-modal-priority"', html)

        self.assertIn(".next-node-priority-badge", css)
        self.assertIn(".badge-priority", css)
        self.assertIn(".child-priority-badge", css)
        self.assertIn(".btn-priority-cycle", css)
        self.assertIn("priority-high", css)
        self.assertIn("priority-normal", css)
        self.assertIn("priority-low", css)

        self.assertIn("next-node-pill", js)
        self.assertIn("child-priority-badge", js)
        self.assertIn("btn-priority-toggle", js)
        self.assertIn("setNodePriority", js)

    def test_favicon_endpoint(self):
        code, _ = self._get("/favicon.ico")
        self.assertEqual(code, 204)


if __name__ == "__main__":
    unittest.main()
