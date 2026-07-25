#!/usr/bin/env python3
"""Dirty-tree guard and byte-safe worktree backup for the prod -> test rsync mirror.

The prod -> test deploy uses ``rsync -a --delete``, which silently deletes
untracked files on the test node and overwrites locally modified tracked files.
This helper lets ``20_sync_code.sh`` refuse to mirror when either side has
uncommitted work, and capture a verifiable backup of the test node's dirty
state (outside the mirror target) before anything is overwritten.

Design constraints (must stay true):
  * Pure standard library, Python 3.9+ compatible so the macOS system
    interpreter at ``/usr/bin/python3`` can run it over SSH with no deps.
  * Byte-safe path handling end to end: filenames may contain non-ASCII
    (Chinese), spaces, or be binary blobs (.docx). Never round-trips a path
    through a locale-dependent text split.
  * Fail closed: any error (not a repo, disk space, tamper, I/O) is a
    non-zero exit so the caller aborts the rsync.

Subcommands:
  is-dirty <repo>          exit 0 clean, 3 dirty, 4 error
  backup   <repo> <dest>   snapshot dirty state into dest (must be outside repo)
  verify   <dest>          recompute and check the backup's SHA-256 manifest
  verify-worktree <repo> <dest>
                           verify dest and prove the dirty tree is unchanged
"""

import hashlib
import os
import shutil
import subprocess
import sys

EXIT_CLEAN = 0
EXIT_USAGE = 2
EXIT_DIRTY = 3
EXIT_FAIL = 4

MANIFEST_NAME = b"SHA256SUMS.nul"
_MIN_FREE_FLOOR = 64 * 1024 * 1024  # always keep this much headroom on the backup volume


def _git(repo, args):
    proc = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    return proc.returncode, proc.stdout, proc.stderr


def _git_output(repo, args, label):
    rc, out, err = _git(repo, args)
    if rc != 0:
        raise RuntimeError("%s failed: %s" % (label, err.decode("utf-8", "replace").strip()))
    return out


def _require_repo(repo):
    rc, _, err = _git(repo, ["rev-parse", "--git-dir"])
    if rc != 0:
        raise RuntimeError("not a git repository: %s (%s)" % (repo, err.decode("utf-8", "replace").strip()))


def _porcelain(repo):
    out = _git_output(repo, ["status", "--porcelain=v1", "-z"], "git status")
    return [entry for entry in out.split(b"\0") if entry]


def is_dirty(repo):
    _require_repo(repo)
    return len(_porcelain(repo)) > 0


def _untracked(repo_bytes):
    out = _git_output(
        repo_bytes.decode("utf-8", "surrogateescape"),
        ["ls-files", "-z", "--others", "--exclude-standard"],
        "git ls-files",
    )
    return [rel for rel in out.split(b"\0") if rel]


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(repo, dest):
    """Snapshot the repo's dirty state into ``dest``. Returns a summary dict.

    Captures: HEAD, branch, the raw porcelain status, the full ``git diff
    --binary HEAD`` (tracked modifications, binary-safe), and every untracked
    file copied byte-for-byte under ``dest/untracked/<relative path>``.
    """
    _require_repo(repo)
    repo_real = os.path.realpath(repo)
    dest_real = os.path.realpath(dest)

    if dest_real == repo_real or dest_real.startswith(repo_real + os.sep):
        raise RuntimeError("backup dest must be outside the repo: %s" % dest)
    if os.path.exists(dest_real) and os.listdir(dest_real):
        raise RuntimeError("backup dest exists and is not empty: %s" % dest)
    os.makedirs(dest_real, exist_ok=True)

    dest_b = dest_real.encode("utf-8", "surrogateescape")
    repo_b = repo_real.encode("utf-8", "surrogateescape")
    written = []  # (absolute bytes path, relative bytes path)

    def _emit(relname, data):
        target = os.path.join(dest_b, relname)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        written.append((target, relname))

    head = _git_output(repo_real, ["rev-parse", "HEAD"], "git rev-parse HEAD")
    branch = _git_output(repo_real, ["rev-parse", "--abbrev-ref", "HEAD"], "git rev-parse branch")
    status = _git_output(repo_real, ["status", "--porcelain=v1", "-z"], "git status")
    diff = _git_output(repo_real, ["diff", "--binary", "HEAD"], "git diff")
    _emit(b"HEAD", head)
    _emit(b"branch", branch)
    _emit(b"status.porcelain.nul", status)
    _emit(b"worktree.diff", diff)

    # Collect real (non-symlink) untracked files and estimate size before copying.
    sources = []
    total = 0
    for rel in _untracked(repo_b):
        src = os.path.join(repo_b, rel)
        if os.path.islink(src):
            raise RuntimeError("untracked symlink cannot be backed up safely: %r" % rel)
        if not os.path.isfile(src):
            raise RuntimeError("untracked path is not a regular file: %r" % rel)
        total += os.path.getsize(src)
        sources.append((src, rel))

    free = shutil.disk_usage(dest_real).free
    if free < total * 2 + _MIN_FREE_FLOOR:
        raise RuntimeError(
            "insufficient free space on backup volume: need ~%d bytes, free %d" % (total * 2 + _MIN_FREE_FLOOR, free)
        )

    untracked_root = os.path.join(dest_b, b"untracked")
    for src, rel in sources:
        target = os.path.join(untracked_root, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        written.append((target, os.path.join(b"untracked", rel)))

    # NUL-delimited manifest: <hex>\0<relpath-bytes>\0 ... (robust to any filename byte).
    manifest = bytearray()
    for abs_path, rel in sorted(written, key=lambda item: item[1]):
        manifest += _sha256_file(abs_path).encode("ascii") + b"\0" + rel + b"\0"
    with open(os.path.join(dest_b, MANIFEST_NAME), "wb") as handle:
        handle.write(bytes(manifest))

    return {"dest": dest_real, "files": len(written), "untracked": len(sources)}


def _manifest_pairs(raw):
    fields = raw.split(b"\0")
    pairs = []
    index = 0
    while index + 1 < len(fields):
        hexv, rel = fields[index], fields[index + 1]
        if hexv == b"" and rel == b"":
            break
        pairs.append((hexv.decode("ascii"), rel))
        index += 2
    return pairs


def _safe_manifest_target(dest_b, rel):
    if not rel or os.path.isabs(rel):
        raise RuntimeError("unsafe backup manifest path")
    parts = rel.split(os.sep.encode())
    if any(part in {b"", b".", b".."} for part in parts):
        raise RuntimeError("unsafe backup manifest path")
    target = os.path.realpath(os.path.join(dest_b, rel))
    dest_real = os.path.realpath(dest_b)
    if target == dest_real or not target.startswith(dest_real + os.sep.encode()):
        raise RuntimeError("backup manifest path escapes destination")
    return target


def verify(dest):
    dest_real = os.path.realpath(dest)
    dest_b = dest_real.encode("utf-8", "surrogateescape")
    manifest_path = os.path.join(dest_b, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise RuntimeError("backup manifest missing: %s" % os.path.join(dest_real, MANIFEST_NAME.decode()))
    with open(manifest_path, "rb") as handle:
        pairs = _manifest_pairs(handle.read())
    if not pairs:
        raise RuntimeError("backup manifest is empty: %s" % dest_real)

    verified = 0
    failed = []
    for hexv, rel in pairs:
        target = _safe_manifest_target(dest_b, rel)
        if not os.path.isfile(target):
            failed.append(rel)
            continue
        if _sha256_file(target) != hexv:
            failed.append(rel)
            continue
        verified += 1
    if failed:
        raise RuntimeError("backup verification failed for %d file(s)" % len(failed))
    return {"verified": verified}


def verify_worktree(repo, dest):
    """Verify a reviewed backup and prove the source dirty tree did not change."""
    _require_repo(repo)
    verification = verify(dest)
    repo_real = os.path.realpath(repo)
    dest_real = os.path.realpath(dest)
    repo_b = repo_real.encode("utf-8", "surrogateescape")
    dest_b = dest_real.encode("utf-8", "surrogateescape")

    expected = {
        b"HEAD": _git_output(repo_real, ["rev-parse", "HEAD"], "git rev-parse HEAD"),
        b"branch": _git_output(repo_real, ["rev-parse", "--abbrev-ref", "HEAD"], "git rev-parse branch"),
        b"status.porcelain.nul": _git_output(repo_real, ["status", "--porcelain=v1", "-z"], "git status"),
        b"worktree.diff": _git_output(repo_real, ["diff", "--binary", "HEAD"], "git diff"),
    }
    for rel, current in expected.items():
        with open(_safe_manifest_target(dest_b, rel), "rb") as handle:
            if handle.read() != current:
                raise RuntimeError("worktree changed after backup: %s" % rel.decode("ascii"))

    backed_untracked = {}
    manifest_path = os.path.join(dest_b, MANIFEST_NAME)
    with open(manifest_path, "rb") as handle:
        for hexv, rel in _manifest_pairs(handle.read()):
            prefix = b"untracked" + os.sep.encode()
            if rel.startswith(prefix):
                backed_untracked[rel[len(prefix):]] = hexv

    current_untracked = {}
    for rel in _untracked(repo_b):
        src = os.path.join(repo_b, rel)
        if os.path.islink(src) or not os.path.isfile(src):
            raise RuntimeError("untracked path is not a regular file: %r" % rel)
        current_untracked[rel] = _sha256_file(src)
    if current_untracked != backed_untracked:
        raise RuntimeError("untracked files changed after backup")
    return {"verified": verification["verified"], "untracked": len(current_untracked)}


def main(argv):
    if not argv:
        sys.stderr.write("usage: sync_guard.py {is-dirty <repo>|backup <repo> <dest>|verify <dest>}\n")
        return EXIT_USAGE
    command = argv[0]
    try:
        if command == "is-dirty":
            dirty = is_dirty(argv[1])
            sys.stdout.write("dirty\n" if dirty else "clean\n")
            return EXIT_DIRTY if dirty else EXIT_CLEAN
        if command == "backup":
            result = backup(argv[1], argv[2])
            sys.stdout.write("backup ok: dest=%s files=%d untracked=%d\n"
                             % (result["dest"], result["files"], result["untracked"]))
            return EXIT_CLEAN
        if command == "verify":
            result = verify(argv[1])
            sys.stdout.write("verify ok: %d files\n" % result["verified"])
            return EXIT_CLEAN
        if command == "verify-worktree":
            result = verify_worktree(argv[1], argv[2])
            sys.stdout.write("verify-worktree ok: files=%d untracked=%d\n"
                             % (result["verified"], result["untracked"]))
            return EXIT_CLEAN
    except IndexError:
        sys.stderr.write("sync_guard: missing argument for %r\n" % command)
        return EXIT_USAGE
    except Exception as exc:  # fail closed: any failure is a hard abort signal
        sys.stderr.write("sync_guard %s failed: %s\n" % (command, exc))
        return EXIT_FAIL
    sys.stderr.write("sync_guard: unknown command %r\n" % command)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
