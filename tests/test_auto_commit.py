#!/usr/bin/env python3
"""
Unit and integration tests for Fault-Tolerant Auto-Commit on Station Completion (Issue #16).
Tests pure Python standard library HTTP endpoints and submodule Git isolation in server.py.
"""

import os
import sys
import stat
import json
import shutil
import tempfile
import threading
import unittest
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from http.server import HTTPServer
from unittest.mock import patch

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import server
from server import GraphRequestHandler, find_available_port, _auto_commit_station_completion


def _rmtree_force(path):
    """Recursively remove a directory tree, clearing Windows read-only flags (e.g. .git objects)."""
    def on_rm_error(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    if Path(path).exists():
        shutil.rmtree(path, onexc=on_rm_error)


class TestAutoCommit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create isolated temporary directory for test graph
        cls.test_dir = tempfile.mkdtemp(prefix="kg_test_autocommit_")
        cls.test_graph_dir = Path(cls.test_dir) / "graph"
        cls.test_graph_dir.mkdir(parents=True, exist_ok=True)

        # Redirect server.GRAPH_DIR to our test folder
        cls.orig_graph_dir = server.GRAPH_DIR
        server.GRAPH_DIR = cls.test_graph_dir

        # Start background HTTP server
        cls.port = find_available_port(9200)
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
        _rmtree_force(cls.test_dir)

    def setUp(self):
        # Clean test_graph_dir before each test
        for p in self.test_graph_dir.iterdir():
            if p.is_dir():
                _rmtree_force(p)
            else:
                try:
                    os.chmod(p, stat.S_IWRITE)
                except Exception:
                    pass
                p.unlink()

    def _init_git_repo(self, repo_dir: Path):
        """Helper to initialize an active git repo with dummy user credentials."""
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "KG Test Bot"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bot@kg.test"], cwd=str(repo_dir), check=True, capture_output=True)

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

    def test_auto_commit_on_completed_status(self):
        """Setting status to completed automatically commits station changes in graph/."""
        self._init_git_repo(self.test_graph_dir)

        # Seed initial station
        station_dir = self.test_graph_dir / "backend" / "auth"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# Authentication\n\n**Status:** in-progress\n", encoding="utf-8")

        # Initial commit in test repo
        subprocess.run(["git", "add", "."], cwd=str(self.test_graph_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial seed"], cwd=str(self.test_graph_dir), check=True, capture_output=True)

        # Update status to completed via API
        status, body = self._post_json("/api/save-status", {
            "path": "backend/auth",
            "status": "completed"
        })

        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))

        # Verify git commit in test_graph_dir
        log_res = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=str(self.test_graph_dir),
            capture_output=True,
            text=True,
            check=True
        )
        self.assertIn("feat(progress): complete auth", log_res.stdout.strip())

        # Verify working directory is clean
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.test_graph_dir),
            capture_output=True,
            text=True,
            check=True
        )
        self.assertEqual(status_res.stdout.strip(), "")

    def test_no_commit_on_non_completed_status(self):
        """Setting status to in-progress or todo does not trigger a git commit."""
        self._init_git_repo(self.test_graph_dir)

        station_dir = self.test_graph_dir / "data" / "sql"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# SQL Station\n\n**Status:** todo\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=str(self.test_graph_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: seed sql"], cwd=str(self.test_graph_dir), check=True, capture_output=True)

        status, body = self._post_json("/api/save-status", {
            "path": "data/sql",
            "status": "in-progress"
        })

        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))

        log_res = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=str(self.test_graph_dir),
            capture_output=True,
            text=True,
            check=True
        )
        self.assertEqual(log_res.stdout.strip(), "chore: seed sql")

    def test_noop_when_not_a_git_repo(self):
        """When graph/ is a plain directory without .git, status update succeeds with no commit."""
        station_dir = self.test_graph_dir / "math" / "linear-algebra"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# Linear Algebra\n\n**Status:** todo\n", encoding="utf-8")

        self.assertFalse((self.test_graph_dir / ".git").exists())

        status, body = self._post_json("/api/save-status", {
            "path": "math/linear-algebra",
            "status": "completed"
        })

        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertIn("completed", info_path.read_text(encoding="utf-8"))
        self.assertFalse((self.test_graph_dir / ".git").exists())

    def test_fault_tolerant_on_git_failure(self):
        """When git commit fails (e.g. non-zero exit code), HTTP request still succeeds with 200."""
        self._init_git_repo(self.test_graph_dir)

        station_dir = self.test_graph_dir / "devops" / "docker"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# Docker\n\n**Status:** todo\n", encoding="utf-8")

        # Mock subprocess.run for 'git commit' to simulate failure exit code 128
        orig_run = subprocess.run

        def failing_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if len(cmd) >= 4 and cmd[0] == "git" and "commit" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="fatal: mock error")
            return orig_run(*args, **kwargs)

        with patch("server.subprocess.run", side_effect=failing_run):
            status, body = self._post_json("/api/save-status", {
                "path": "devops/docker",
                "status": "completed"
            })

        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertIn("completed", info_path.read_text(encoding="utf-8"))

    def test_fault_tolerant_on_git_timeout(self):
        """When git command times out, HTTP request succeeds cleanly without error."""
        self._init_git_repo(self.test_graph_dir)

        station_dir = self.test_graph_dir / "cloud" / "k8s"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# K8s\n\n**Status:** todo\n", encoding="utf-8")

        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        with patch("server.subprocess.run", side_effect=timeout_run):
            status, body = self._post_json("/api/save-status", {
                "path": "cloud/k8s",
                "status": "completed"
            })

        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"))
        self.assertIn("completed", info_path.read_text(encoding="utf-8"))

    def test_root_repo_remains_untouched_when_subfolder_lacks_git(self):
        """
        Verify that if GRAPH_DIR is inside a parent git repo but GRAPH_DIR lacks its own .git,
        auto-commit strictly refuses to stage or commit to the parent repo.
        """
        parent_repo = Path(self.test_dir) / "mock_parent_repo"
        parent_repo.mkdir(parents=True, exist_ok=True)
        self._init_git_repo(parent_repo)

        # Create graph folder as a subfolder without .git
        sub_graph = parent_repo / "graph"
        sub_graph.mkdir(parents=True, exist_ok=True)
        station_dir = sub_graph / "station_x"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_file = station_dir / "info.md"
        info_file.write_text("# Station X\n\n**Status:** completed\n", encoding="utf-8")

        # Initial commit in parent
        subprocess.run(["git", "add", "."], cwd=str(parent_repo), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: parent init"], cwd=str(parent_repo), check=True, capture_output=True)

        try:
            # Modify station file
            info_file.write_text("# Station X\n\n**Status:** completed\n*Updated*", encoding="utf-8")

            # Call helper with sub_graph as target_graph_dir
            _auto_commit_station_completion(station_dir, "station_x", graph_dir=sub_graph)

            # Parent repo log must NOT contain any auto-commit
            log_res = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"],
                cwd=str(parent_repo),
                capture_output=True,
                text=True,
                check=True
            )
            self.assertEqual(log_res.stdout.strip(), "chore: parent init")

            # Parent repo index should NOT have staged changes
            diff_res = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=str(parent_repo),
                capture_output=True,
                text=True,
                check=True
            )
            self.assertEqual(diff_res.stdout.strip(), "")
        finally:
            _rmtree_force(parent_repo)

    def test_no_commit_when_no_changes_staged(self):
        """When station is already completed and no disk changes exist, auto-commit cleanly exits."""
        self._init_git_repo(self.test_graph_dir)

        station_dir = self.test_graph_dir / "ai" / "nlp"
        station_dir.mkdir(parents=True, exist_ok=True)
        info_path = station_dir / "info.md"
        info_path.write_text("# NLP\n\n**Status:** completed\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=str(self.test_graph_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat(progress): complete nlp"], cwd=str(self.test_graph_dir), check=True, capture_output=True)

        # Invoke helper again when already clean
        _auto_commit_station_completion(station_dir, "nlp", graph_dir=self.test_graph_dir)

        log_res = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=str(self.test_graph_dir),
            capture_output=True,
            text=True,
            check=True
        )
        self.assertEqual(log_res.stdout.strip(), "feat(progress): complete nlp")


if __name__ == "__main__":
    unittest.main()
