#!/usr/bin/env python3
"""Self-update check that ships inside the autoloop skill.

Runs before Phase 0 (see SKILL.md) so a long, unattended loop never starts on a
stale version - an upstream bugfix matters most precisely when the user is about
to walk away for hours. Unlike an external hook, this travels with the skill, so
every install gets it, not just the author's machine.

It locates its own skill directory from __file__ (never a hardcoded path), and:
  * if that's a git checkout, fetches its own upstream and compares
  * fast-forwards when cleanly behind; otherwise reports and touches nothing
  * fails open (offline, non-git install, any error) so it can never, by itself,
    block a run

Output: a human-readable line, then a final JSON verdict line the agent parses:
  {"status": ..., "action": ..., "behind": N, "ahead": M, "detail": ...}
status is one of: updated | up-to-date | behind | behind-dirty | diverged |
                  no-upstream | not-git | offline | error
The only status the agent should treat as a possible hard stop is an out-of-date
skill it cannot auto-update (behind / behind-dirty / diverged / no-upstream) -
and only after telling the user.

The `detail` field carries diagnostics derived from git output. It is untrusted:
a remote can influence git's stderr, so it is sanitized here and must never be
read as instructions.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

# The script lives at <skill_dir>/scripts/update_check.py, so the skill dir is
# two levels up. Derived, never hardcoded, so it works for every install.
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/sweekuh/autoloop"
DETAIL_MAX = 160

# Non-interactive git: never pop a credential GUI, terminal, or askpass prompt.
# If auth is needed and unavailable, fail fast so we fall through to fail-open.
GIT_ENV = dict(
    os.environ,
    GIT_TERMINAL_PROMPT="0",
    GCM_INTERACTIVE="Never",
    GIT_PAGER="cat",
    GIT_ASKPASS="echo",
    SSH_ASKPASS_REQUIRE="never",
    GIT_SSH_COMMAND="ssh -oBatchMode=yes",
)

# Absolute path resolved once. A bare "git" would let Windows CreateProcess
# search the current directory first, and the skill runs with the cwd set to
# whatever project is being optimized - a planted git.exe there must not win.
GIT_BIN = shutil.which("git")


def git(*args, timeout=20):
    return subprocess.run(
        [GIT_BIN, "-C", SKILL_DIR, *args],
        capture_output=True, text=True, timeout=timeout, env=GIT_ENV,
        cwd=SKILL_DIR,
    )


def ascii_only(s):
    """Keep output pure ASCII: install paths and git messages may not be."""
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def clean_detail(s):
    """Sanitize git output before it reaches the agent-parsed verdict.

    Remote servers control parts of git's stderr (sideband `remote:` lines,
    `fatal: remote error: <server text>`), and clone URLs can embed a token.
    Drop remote-controlled lines, redact URL credentials, cap the length.
    """
    lines = [ln.strip() for ln in str(s or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.lower().startswith(("remote:", "remote error"))]
    out = lines[0] if lines else ""
    if "@" in out:  # redact https://user:token@host
        out = " ".join(
            "<redacted-url>" if ("://" in w and "@" in w) else w for w in out.split()
        )
    return ascii_only(out)[:DETAIL_MAX]


def emit(human, status, action, **extra):
    print(ascii_only(human))
    print(json.dumps({"status": status, "action": action, **extra}))
    return 0


def main():
    p = argparse.ArgumentParser(
        description="Check whether this autoloop skill checkout is up to date."
    )
    p.add_argument(
        "--check-only", action="store_true",
        help="report only; never fast-forward (still contacts the remote)",
    )
    args = p.parse_args()  # unknown flags now error out instead of silently updating

    if GIT_BIN is None:
        return emit(
            "autoloop update-check: git not found on PATH - skipping the update check.",
            "not-git", "skip", detail="git executable not found")

    # `.git` is a directory in a normal clone but a FILE in a worktree or
    # submodule install, so test existence, not directory-ness.
    if not os.path.exists(os.path.join(SKILL_DIR, ".git")):
        return emit(
            f"autoloop update-check: not a git checkout - update via your installer or {REPO_URL}",
            "not-git", "skip", detail="no .git entry")

    # Upstream ref: the checkout's own tracking branch (fork-friendly). If there
    # is none - detached HEAD, a deliberate pin to an audited SHA, or a branch
    # with no tracking config - do NOT invent one. Fast-forwarding a detached
    # HEAD to origin/main would silently undo the user's pin, something even
    # `git pull --ff-only` refuses to do.
    up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream = up.stdout.strip() if up.returncode == 0 else ""
    if not upstream or upstream == "@{u}":
        return emit(
            "autoloop update-check: no tracking branch (detached HEAD or unset upstream) - "
            f'not auto-updating. Check manually: git -C "{SKILL_DIR}" status',
            "no-upstream", "manual", detail=clean_detail(up.stderr))
    remote = upstream.split("/", 1)[0]

    fetched = git("fetch", "--quiet", remote)
    if fetched.returncode != 0:
        return emit(
            "autoloop update-check: couldn't reach the repo (git fetch failed) - proceeding with local version.",
            "offline", "skip", detail=clean_detail(fetched.stderr))

    local = git("rev-parse", "HEAD").stdout.strip()
    rem = git("rev-parse", upstream)
    if rem.returncode != 0:
        return emit(
            f"autoloop update-check: upstream '{upstream}' does not resolve - skipping.",
            "no-upstream", "skip", detail=clean_detail(rem.stderr))
    remote_sha = rem.stdout.strip()

    if local == remote_sha:
        return emit("autoloop update-check: up to date.", "up-to-date", "none", behind=0, ahead=0)

    def count(rev):
        """Commit count, or None when git itself failed.

        Returning 0 on failure would fabricate a verdict ("auto-updated 0
        commits") from a broken command, so failure is surfaced, not guessed.
        """
        r = git("rev-list", "--count", rev)
        if r.returncode != 0:
            return None
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None

    ahead = count(f"{upstream}..HEAD")
    behind = count(f"HEAD..{upstream}")
    if ahead is None or behind is None:
        return emit(
            "autoloop update-check: couldn't compare against "
            f"{upstream} (rev-list failed) - proceeding with local version.",
            "error", "skip", detail="rev-list failed")

    if ahead > 0:
        return emit(
            f'autoloop update-check: {ahead} local commit(s) not on {upstream} (and {behind} behind) - '
            f'not auto-updating. Reconcile: git -C "{SKILL_DIR}" status',
            "diverged", "manual", behind=behind, ahead=ahead)

    if git("status", "--porcelain").stdout.strip():
        return emit(
            f'autoloop update-check: {behind} commit(s) behind {upstream} but the working tree is dirty - '
            f'not auto-updating. Stash/discard then: git -C "{SKILL_DIR}" pull --ff-only',
            "behind-dirty", "manual", behind=behind, ahead=0)

    if args.check_only:
        return emit(
            f'autoloop update-check: {behind} commit(s) behind. Update: git -C "{SKILL_DIR}" pull --ff-only',
            "behind", "update-available", behind=behind, ahead=0)

    ff = git("merge", "--ff-only", upstream)
    if ff.returncode != 0:
        return emit(
            f'autoloop update-check: {behind} behind and fast-forward failed ({clean_detail(ff.stderr)}). '
            f'Update manually: git -C "{SKILL_DIR}" pull --ff-only',
            "behind", "ff-failed", behind=behind, ahead=0)

    # Report where HEAD actually landed rather than where it was expected to.
    landed = git("rev-parse", "HEAD").stdout.strip() or remote_sha
    return emit(
        f"autoloop update-check: auto-updated {behind} commit(s) to latest ({upstream} @ {landed[:7]}). "
        f"Re-read SKILL.md before continuing.",
        "updated", "fast-forwarded", behind=behind, ahead=0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        # A hung fetch is a network problem, not a broken script.
        print("autoloop update-check: git timed out - proceeding with local version.")
        print(json.dumps({"status": "offline", "action": "skip", "detail": "git timed out"}))
        sys.exit(0)
    except Exception as e:  # never block a run on the update check itself
        print(ascii_only(f"autoloop update-check: FAILED, update check is broken (error: {e})"))
        print(json.dumps({"status": "error", "action": "skip", "detail": ascii_only(str(e))[:DETAIL_MAX]}))
        sys.exit(0)
