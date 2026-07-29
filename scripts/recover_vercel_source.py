#!/usr/bin/env python3
"""Recover the source of a Vercel CLI deployment.

Projects deployed with `vercel deploy` (rather than from Git) keep their
uploaded source tree on Vercel. This downloads it back to a local folder so it
can be put under version control.

Usage:
    export VERCEL_TOKEN=...            # vercel.com/account/tokens
    python3 recover_vercel_source.py \
        --deployment dpl_xxxxxxxxxxxx \
        --team team_xxxxxxxxxxxx \
        --out ./medicalai-frontend

Only the Python standard library is required.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.vercel.com"

# Never recreate these locally — they are build artefacts or secrets.
SKIP_DIRS = {"node_modules", ".git", ".vercel", "dist", "build", ".next"}


def request(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {e.code} for {url}\n{detail}")


def list_tree(deployment: str, team: str, token: str):
    q = urllib.parse.urlencode({"teamId": team}) if team else ""
    url = f"{API}/v6/deployments/{deployment}/files" + (f"?{q}" if q else "")
    return json.loads(request(url, token))


def file_contents(deployment: str, file_id: str, team: str, token: str) -> bytes:
    q = urllib.parse.urlencode({"teamId": team}) if team else ""
    url = f"{API}/v8/deployments/{deployment}/files/{file_id}" + (f"?{q}" if q else "")
    raw = request(url, token)

    # Documented as JSON carrying base64, but the endpoint has also served the
    # file straight through — accept either rather than corrupting the output.
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw

    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        try:
            return base64.b64decode(data)
        except Exception:
            return data.encode("utf-8")
    return raw


def walk(entries, deployment, team, token, out: Path, prefix=Path(".")):
    saved = skipped = 0
    for entry in entries:
        name = entry.get("name", "")
        kind = entry.get("type")
        rel = prefix / name

        if kind == "directory":
            if name in SKIP_DIRS:
                print(f"  skip  {rel}/")
                skipped += 1
                continue
            s, k = walk(entry.get("children") or [], deployment, team, token, out, rel)
            saved += s
            skipped += k
        elif kind == "file":
            uid = entry.get("uid")
            if not uid:
                skipped += 1
                continue
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file_contents(deployment, uid, team, token))
            print(f"  ok    {rel}")
            saved += 1
        else:
            skipped += 1
    return saved, skipped


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--deployment", required=True, help="Deployment id (dpl_...)")
    p.add_argument("--team", default="", help="Team id (team_...)")
    p.add_argument("--out", default="./recovered-source", help="Output directory")
    args = p.parse_args()

    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        sys.exit("Set VERCEL_TOKEN first — create one at vercel.com/account/tokens")

    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} already exists and is not empty — choose an empty directory")

    print(f"Reading file tree of {args.deployment} ...")
    tree = list_tree(args.deployment, args.team, token)
    if not tree:
        sys.exit("Deployment has no retrievable source tree. This only works for "
                 "deployments made with the Vercel CLI or the API 'files' key.")

    saved, skipped = walk(tree, args.deployment, args.team, token, out)
    print(f"\nDone: {saved} files written to {out} ({skipped} skipped)")
    print("\nNext:")
    print(f"  cd {out}")
    print("  git init && git add -A && git commit -m 'Recover frontend source'")


if __name__ == "__main__":
    main()
