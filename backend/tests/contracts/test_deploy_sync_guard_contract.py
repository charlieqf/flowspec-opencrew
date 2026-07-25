"""Contract test for scripts/deploy/sync_guard.py (dirty-tree mirror guard).

Runs under the repo's standard contract runner
(``python -m pytest -q backend/tests/contracts``). Exercises the
byte-safety guarantees the guard must uphold: non-ASCII (Chinese) names, spaces,
binary/.docx blobs with NUL bytes, and nested directories -- the exact classes
of file that a naive line/text split silently dropped during the incident this
guard exists to prevent.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPO_ROOT / "scripts" / "deploy" / "sync_guard.py"
SYNC_SCRIPT_PATH = REPO_ROOT / "scripts" / "deploy" / "20_sync_code.sh"
SYNC_LIBRARY_PATH = REPO_ROOT / "scripts" / "deploy" / "lib_sync_guard.sh"


def _load_guard():
    spec = importlib.util.spec_from_file_location("sync_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "guard@test")
    _git(root, "config", "user.name", "guard test")
    (root / "README.md").write_text("base\n")
    _git(root, "add", "README.md")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "base")


# Tricky untracked filenames + payloads the guard must preserve byte-for-byte.
TRICKY_SAMPLES = {
    "docs/Koubo_设计文档_v2.md": "设计内容：统一资源绑定\n".encode("utf-8"),
    "docs/名字 有空格.md": b"a name with spaces",
    "docs/TTS_用户操作说明.docx": bytes(range(256)) * 8,  # binary blob incl. NUL
    "nested/a/b/c/deep.bin": b"\x00\x01\x02\xff\xfe\x00",
}


class DeploySyncGuardContract(unittest.TestCase):
    def test_guard_script_present(self):
        self.assertTrue(GUARD_PATH.is_file(), "sync_guard.py must ship with the deploy scripts")

    def test_clean_repo_is_not_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            self.assertFalse(guard.is_dirty(str(repo)))

    def test_untracked_non_ascii_marks_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "设计文档 v2.md").write_text("x")  # Chinese + space, untracked
            self.assertTrue(guard.is_dirty(str(repo)))

    def test_modified_tracked_marks_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "README.md").write_text("changed\n")
            self.assertTrue(guard.is_dirty(str(repo)))

    def test_backup_preserves_tricky_files_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            for rel, data in TRICKY_SAMPLES.items():
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (repo / "README.md").write_text("changed tracked\n")

            dest = Path(tmp) / "backup" / "snapshot"
            result = guard.backup(str(repo), str(dest))

            self.assertEqual(result["untracked"], len(TRICKY_SAMPLES))
            for rel, data in TRICKY_SAMPLES.items():
                backed = dest / "untracked" / rel
                self.assertTrue(backed.is_file(), rel)
                self.assertEqual(backed.read_bytes(), data, rel)

            # Tracked modification is captured in the binary diff.
            self.assertIn(b"changed tracked", (dest / "worktree.diff").read_bytes())
            # A fresh backup verifies against its own manifest.
            self.assertEqual(guard.verify(str(dest))["verified"], result["files"])

    def test_verify_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "证据 文件.txt").write_bytes(b"original")
            dest = Path(tmp) / "backup"
            guard.backup(str(repo), str(dest))
            (dest / "untracked" / "证据 文件.txt").write_bytes(b"tampered payload")
            with self.assertRaises(Exception):
                guard.verify(str(dest))

    def test_verify_worktree_accepts_unchanged_reviewed_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "README.md").write_text("changed\n")
            (repo / "设计 文档.docx").write_bytes(b"\x00binary\xff")
            dest = Path(tmp) / "backup"
            result = guard.backup(str(repo), str(dest))

            verified = guard.verify_worktree(str(repo), str(dest))
            self.assertEqual(verified["verified"], result["files"])
            self.assertEqual(verified["untracked"], 1)

    def test_verify_worktree_rejects_changes_after_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            untracked = repo / "设计 文档.docx"
            untracked.write_bytes(b"before")
            dest = Path(tmp) / "backup"
            guard.backup(str(repo), str(dest))

            untracked.write_bytes(b"after")
            with self.assertRaises(Exception):
                guard.verify_worktree(str(repo), str(dest))

    def test_verify_rejects_manifest_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "untracked.txt").write_text("x")
            dest = Path(tmp) / "backup"
            guard.backup(str(repo), str(dest))
            manifest = dest / guard.MANIFEST_NAME.decode()
            manifest.write_bytes(b"0" * 64 + b"\0../outside\0")
            with self.assertRaises(Exception):
                guard.verify(str(dest))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_backup_fails_closed_for_untracked_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside")
            os.symlink(str(outside), str(repo / "untracked-link"))
            with self.assertRaises(Exception):
                guard.backup(str(repo), str(Path(tmp) / "backup"))

    def test_backup_refuses_dest_inside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "u.txt").write_text("x")
            with self.assertRaises(Exception):
                guard.backup(str(repo), str(repo / "inside"))

    def test_backup_refuses_nonempty_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "u.txt").write_text("x")
            dest = Path(tmp) / "backup"
            dest.mkdir()
            (dest / "preexisting").write_text("keep")
            with self.assertRaises(Exception):
                guard.backup(str(repo), str(dest))

    def test_is_dirty_on_non_repo_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                guard.is_dirty(tmp)

    def test_sync_requires_explicit_reviewed_backup_path_before_rsync(self):
        script = SYNC_SCRIPT_PATH.read_text(encoding="utf-8")
        library = SYNC_LIBRARY_PATH.read_text(encoding="utf-8")
        self.assertIn("--force-after-reviewed-backup /absolute/reviewed/backup", script)
        self.assertLess(script.index("sync_guard_preflight"), script.index("rsync -a --delete"))
        self.assertIn("verify-worktree", library)
        self.assertNotIn('if [[ "$force" == "yes" ]]', library)


if __name__ == "__main__":
    unittest.main()
