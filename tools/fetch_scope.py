#!/usr/bin/env python3
"""
fetch_scope.py — pull Immunefi in-scope Solidity source at the exact pinned commit.

Give it the list of GitHub URLs copied from an Immunefi program's Scope tab
(one per line, extra text is ignored). It will:
  1. parse every github.com blob/tree URL -> (owner, repo, ref, path)
  2. group by (owner, repo, ref)  [ref is usually the pinned commit SHA]
  3. clone each repo AT THAT EXACT REF into repos/<repo>@<ref8>/
  4. verify each in-scope path exists, copy it into scope-src/ (flattened tree)
  5. write scope-manifest.tsv  (the "what is actually in scope" table)

Why clone the whole repo (not just the files): you need imports, deps, and the
foundry/hardhat config to compile and navigate. The manifest tells you which
files are the in-scope assets vs. dependencies.

Usage:
    python3 fetch_scope.py scope-urls.txt
    python3 fetch_scope.py scope-urls.txt --files-only   # skip clone, raw-download just the listed files
    pbpaste | python3 fetch_scope.py -                    # read URLs from stdin

Notes:
  - A renamed repo (e.g. silo-contracts-v2 -> v3) still clones fine; git follows the redirect.
  - Set GITHUB_TOKEN in env to raise API/raw rate limits (optional).
"""
import os, re, sys, subprocess, shutil, urllib.request, json
from collections import defaultdict

BLOB_RE = re.compile(
    r"https?://github\.com/([^/\s]+)/([^/\s]+)/(?:blob|tree|raw)/([^/\s]+)/([^\s)\"'>]+)"
)
HEXSHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_urls(src):
    text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    seen, out = set(), []
    for m in BLOB_RE.finditer(text):
        owner, repo, ref, path = m.groups()
        path = path.split("#")[0].split("?")[0]
        key = (owner, repo, ref, path)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    if check and r.returncode != 0:
        log("  ! cmd failed:", " ".join(cmd))
        log("   ", r.stdout.strip()[-800:])
    return r.returncode, r.stdout


def clone_at(owner, repo, ref, dest):
    """Clone <owner>/<repo> at <ref> (commit sha, tag, or branch) into dest."""
    if os.path.isdir(os.path.join(dest, ".git")):
        log(f"  = already cloned: {dest}")
        return True
    url = f"https://github.com/{owner}/{repo}.git"
    os.makedirs(dest, exist_ok=True)
    if HEXSHA_RE.match(ref):
        # commit sha: init + fetch that exact object (GitHub allows SHA fetch)
        run(["git", "init", "-q"], cwd=dest)
        run(["git", "remote", "add", "origin", url], cwd=dest)
        rc, _ = run(["git", "fetch", "-q", "--depth", "1", "origin", ref], cwd=dest, check=False)
        if rc != 0:
            log("  … shallow SHA fetch failed, retrying full fetch (slower)")
            rc, _ = run(["git", "fetch", "-q", "origin"], cwd=dest, check=False)
            if rc != 0:
                return False
        rc, _ = run(["git", "checkout", "-q", ref], cwd=dest, check=False)
        if rc != 0:
            rc, _ = run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, check=False)
        return rc == 0
    else:
        # branch or tag
        shutil.rmtree(dest, ignore_errors=True)
        rc, _ = run(["git", "clone", "-q", "--depth", "1", "--branch", ref,
                     "--single-branch", url, dest], check=False)
        if rc != 0:
            rc, _ = run(["git", "clone", "-q", url, dest], check=False)
            if rc == 0:
                run(["git", "checkout", "-q", ref], cwd=dest, check=False)
        return rc == 0


def raw_download(owner, repo, ref, path, dest_file):
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    req = urllib.request.Request(raw)
    if TOKEN:
        req.add_header("Authorization", f"token {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        open(dest_file, "wb").write(data)
        return True
    except Exception as e:
        log(f"  ! raw download failed {path}: {e}")
        return False


def main():
    if len(sys.argv) < 2:
        log(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    files_only = "--files-only" in sys.argv[2:]

    here = os.path.dirname(os.path.abspath(__file__))
    repos_dir = os.path.join(here, "repos")
    src_dir = os.path.join(here, "scope-src")
    os.makedirs(repos_dir, exist_ok=True)
    os.makedirs(src_dir, exist_ok=True)

    assets = read_urls(src)
    if not assets:
        log("No github blob/tree URLs found in input. Paste the Scope tab URLs (one per line).")
        sys.exit(1)

    groups = defaultdict(list)
    for owner, repo, ref, path in assets:
        groups[(owner, repo, ref)].append(path)

    log(f"Parsed {len(assets)} in-scope files across {len(groups)} repo@ref group(s):")
    for (owner, repo, ref), paths in groups.items():
        log(f"  - {owner}/{repo} @ {ref[:12]}  ({len(paths)} files)")

    manifest = []  # (owner, repo, ref, path, exists)
    for (owner, repo, ref), paths in groups.items():
        tag = f"{repo}@{ref[:8]}"
        if files_only:
            for p in paths:
                dest = os.path.join(src_dir, tag, p)
                ok = raw_download(owner, repo, ref, p, dest)
                manifest.append((owner, repo, ref, p, ok))
            continue
        dest_repo = os.path.join(repos_dir, tag)
        log(f"\n>> cloning {owner}/{repo} @ {ref} …")
        ok = clone_at(owner, repo, ref, dest_repo)
        if not ok:
            log(f"  ! clone failed; falling back to raw file download for {tag}")
            for p in paths:
                dfile = os.path.join(src_dir, tag, p)
                exists = raw_download(owner, repo, ref, p, dfile)
                manifest.append((owner, repo, ref, p, exists))
            continue
        for p in paths:
            srcf = os.path.join(dest_repo, p)
            exists = os.path.isfile(srcf)
            if exists:
                dfile = os.path.join(src_dir, tag, p)
                os.makedirs(os.path.dirname(dfile), exist_ok=True)
                shutil.copy2(srcf, dfile)
            else:
                log(f"  ! in-scope path NOT found in clone: {p}")
            manifest.append((owner, repo, ref, p, exists))

    man_path = os.path.join(here, "scope-manifest.tsv")
    with open(man_path, "w", encoding="utf-8") as f:
        f.write("owner\trepo\tref\tpath\tin_scope\texists\n")
        for owner, repo, ref, p, exists in manifest:
            f.write(f"{owner}\t{repo}\t{ref}\t{p}\tYES\t{'yes' if exists else 'MISSING'}\n")

    missing = sum(1 for *_ , e in manifest if not e)
    log(f"\nDONE. {len(manifest)} in-scope files, {missing} missing.")
    log(f"  manifest : {man_path}")
    log(f"  flat src : {src_dir}/  (in-scope files only, for fast reading)")
    if not files_only:
        log(f"  full repo: {repos_dir}/  (clone at pinned commit, for compile/imports)")
    if missing:
        log("  ⚠ MISSING files = the URL ref/path may be truncated or wrong — recheck those lines.")


if __name__ == "__main__":
    main()
