#!/usr/bin/env python3
"""Put the frontend source under version control, in one run.

Finds the project locally if it is still on this machine, otherwise recovers it
from its Vercel deployment, then initialises a git repository, commits, and
pushes to GitHub.

Usage:
    # if the folder may still exist locally, this finds it on its own
    python3 frontend_to_git.py --repo holicpeter/medicalai-frontend

    # recovering from Vercel needs a token from vercel.com/account/tokens
    set VERCEL_TOKEN=...
    python3 frontend_to_git.py --repo holicpeter/medicalai-frontend \
        --deployment dpl_9iHBrZfoTkdExZe4nh2tMmUtNgq9 \
        --team team_2ER9zfcQsXaxWcEr3mBCHkUL

Creating the GitHub repository is done with the `gh` CLI when it is installed;
otherwise the script stops and tells you the one page to click through.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARKERS = ("vite.config.js", "vite.config.ts", "vite.config.mjs")

GITIGNORE = """\
node_modules/
dist/
.vercel/
.env
.env.local
*.log
.DS_Store
"""


def run(cmd, cwd=None, check=True, capture=False):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        capture_output=capture, text=True,
    )


def find_local_project(search_root: Path):
    """Look for a Vite project, skipping the noise a full-disk walk hits."""
    print(f"Searching {search_root} for a Vite project ...")
    skip = {"node_modules", ".git", "AppData", "Windows", "Program Files",
            "$Recycle.Bin", ".cache", "Library"}
    for root, dirs, files in os.walk(search_root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith("$")]
        for marker in MARKERS:
            if marker in files:
                found = Path(root)
                if (found / "package.json").exists():
                    print(f"  found: {found}")
                    return found
    print("  nothing found locally")
    return None


def recover_from_vercel(deployment, team, out: Path):
    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        sys.exit("The project is not on this machine and VERCEL_TOKEN is not set.\n"
                 "Create one at vercel.com/account/tokens, then re-run with\n"
                 "  --deployment <dpl_...> --team <team_...>")
    if not deployment:
        sys.exit("Pass --deployment <dpl_...> to recover the source from Vercel.")

    script = Path(__file__).with_name("recover_vercel_source.py")
    if not script.exists():
        sys.exit(f"Missing {script}. Download it from the medicalai-backend repo "
                 "(scripts/recover_vercel_source.py) next to this file.")

    print("Recovering source from the Vercel deployment ...")
    run([sys.executable, str(script),
         "--deployment", deployment, "--team", team or "", "--out", str(out)])
    return out


def ensure_git_repo(project: Path, repo: str, branch: str):
    if not shutil.which("git"):
        sys.exit("git is not installed or not on PATH.")

    gitignore = project / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
        print("  wrote .gitignore")

    if not (project / ".git").exists():
        run(["git", "init"], cwd=project)

    run(["git", "add", "-A"], cwd=project)

    staged = run(["git", "diff", "--cached", "--name-only"],
                 cwd=project, capture=True).stdout.strip()
    if staged:
        run(["git", "commit", "-m", "Add frontend source"], cwd=project)
    else:
        print("  nothing new to commit")

    run(["git", "branch", "-M", branch], cwd=project)

    remotes = run(["git", "remote"], cwd=project, capture=True).stdout.split()
    if "origin" not in remotes:
        run(["git", "remote", "add", "origin",
             f"https://github.com/{repo}.git"], cwd=project)


def create_github_repo(repo: str):
    if not shutil.which("gh"):
        print("\n`gh` is not installed, so the repository has to be created by hand:")
        print(f"  1. open https://github.com/new")
        print(f"  2. name it {repo.split('/')[-1]}, leave it empty (no README)")
        print("  3. re-run this script, or just `git push -u origin main`")
        return False

    existing = run(["gh", "repo", "view", repo], check=False, capture=True)
    if existing.returncode == 0:
        print(f"  repository {repo} already exists")
        return True

    print(f"Creating private repository {repo} ...")
    run(["gh", "repo", "create", repo, "--private"])
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, help="GitHub target, e.g. owner/name")
    p.add_argument("--path", help="Project folder, if you already know it")
    p.add_argument("--search-root", default=str(Path.home()),
                   help="Where to search for the project (default: home directory)")
    p.add_argument("--deployment", help="Vercel deployment id, for recovery")
    p.add_argument("--team", default="", help="Vercel team id, for recovery")
    p.add_argument("--branch", default="main")
    p.add_argument("--no-push", action="store_true", help="Commit but do not push")
    args = p.parse_args()

    if args.path:
        project = Path(args.path).resolve()
        if not project.exists():
            sys.exit(f"{project} does not exist")
    else:
        project = find_local_project(Path(args.search_root))
        if project is None:
            project = recover_from_vercel(
                args.deployment, args.team,
                Path.home() / args.repo.split("/")[-1],
            )

    print(f"\nProject: {project}")
    ensure_git_repo(project, args.repo, args.branch)

    if args.no_push:
        print("\nCommitted. Skipping push as requested.")
        return

    if create_github_repo(args.repo):
        print("\nPushing ...")
        run(["git", "push", "-u", "origin", args.branch], cwd=project)
        print(f"\nDone: https://github.com/{args.repo}")


if __name__ == "__main__":
    main()
