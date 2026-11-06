#!/usr/bin/env python3
# Part of the review kit; Apache-2.0, as the kit. Where it came from is recorded by the project
# that uses the kit, not here: the kit holds no project facts.
"""Mint a GitHub App JWT or installation access token.

Dependency-free: Python standard library plus the `openssl` binary for RS256.

    python3 -I mint-app-token.py --app-id ID --key PEM --jwt-only
    python3 -I mint-app-token.py --app-id ID --key PEM --installation-id ID
                                 [--repo NAME ...] [--permission NAME=LEVEL ...]
                                 [--all-permissions] [--api-url URL]

--jwt-only prints the App's JWT (valid 9 minutes), for `GET /app` and
`GET /app/installations/<id>`. Otherwise the installation token (`ghs_...`, valid 1 hour)
is printed on stdout and its expiry, repositories and permissions on stderr.

--repo (repeatable) limits the token to those repositories of the installation (names
without the owner); without it the token covers every repository the installation was
granted. --permission (repeatable) asks for a narrower permission set; without it the
default below is requested, which never includes administration or workflows. A token can
never exceed what the App and the installation were granted (GitHub docs). Use
--all-permissions to request no narrowing at all.

Exit status: 0 success; 1 GitHub refused the request, could not be reached, or answered
without a token; 2 bad arguments or setup.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

# A typical agent set: push branches and open pull requests; read CI results.
DEFAULT_PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
    "metadata": "read",
    "actions": "read",
    "checks": "read",
    "statuses": "read",
}
LEVELS = ("read", "write", "admin")
# GitHub App ids are numbers; client ids are "Iv" + letters, digits, dots (e.g. Iv23li...).
APP_ID_RE = re.compile(r"[0-9]+|Iv[0-9A-Za-z.]+")
INSTALLATION_ID_RE = re.compile(r"[0-9]+")
# A repository name without the owner (GitHub: letters, digits, ".", "-", "_").
REPO_RE = re.compile(r"[A-Za-z0-9._-]+")
PERMISSION_RE = re.compile(r"[a-z][a-z0-9_]*")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(app_id: str, key_path: str) -> str:
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    # GitHub: iat 60 s in the past against clock drift, exp at most 10 min after iat.
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id},
                                separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    proc = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_path],
                          input=signing_input, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"openssl could not sign with {key_path}: "
                         f"{proc.stderr.decode(errors='replace').strip()}")
    return f"{header}.{payload}.{b64url(proc.stdout)}"


def request(api_url: str, path: str, jwt_token: str, body: dict | None) -> dict:
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {jwt_token}",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "mint-app-token"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except ValueError as e:
        print(f"GitHub's answer for {path} is not JSON: {e}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        print(f"GitHub answered {e.code} for {path}: {detail}", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"cannot reach {api_url}: {e.reason}", file=sys.stderr)
        raise SystemExit(1)


def parse_permissions(items: list[str]) -> dict:
    perms = {}
    for item in items:
        name, sep, level = item.partition("=")
        name = name.replace("-", "_")
        if not sep or not PERMISSION_RE.fullmatch(name) or level not in LEVELS:
            raise SystemExit(f"--permission {item!r}: expected NAME=read|write|admin")
        perms[name] = level
    return perms


def main() -> int:
    p = argparse.ArgumentParser(prog="mint-app-token.py",
                                description="Mint a GitHub App JWT or installation token.")
    p.add_argument("--app-id", required=True, help="the App ID (or client ID)")
    p.add_argument("--key", required=True, help="the App's private key (.pem)")
    p.add_argument("--installation-id", help="required unless --jwt-only")
    p.add_argument("--repo", action="append", default=[],
                   help="limit the token to this repository name (repeatable)")
    p.add_argument("--permission", action="append", default=[], metavar="NAME=LEVEL",
                   help="request this permission (repeatable; replaces the default set)")
    p.add_argument("--all-permissions", action="store_true",
                   help="request every permission the installation has (no narrowing)")
    p.add_argument("--api-url", default="https://api.github.com")
    p.add_argument("--jwt-only", action="store_true", help="print the App JWT and stop")
    try:
        a = p.parse_args()
    except SystemExit as e:
        return 2 if e.code else 0

    try:
        if shutil.which("openssl") is None:
            raise SystemExit("the openssl binary is not on PATH")
        if not os.path.isfile(a.key):
            raise SystemExit(f"no key file at {a.key}")
        if os.stat(a.key).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"warning: {a.key} is readable by group or others; use chmod 600",
                  file=sys.stderr)
        if a.permission and a.all_permissions:
            raise SystemExit("--permission and --all-permissions exclude each other")
        if not APP_ID_RE.fullmatch(a.app_id):
            raise SystemExit(f"--app-id {a.app_id!r}: expected a number or a client id (Iv...)")
        if not a.jwt_only and not a.installation_id:
            raise SystemExit("--installation-id is required unless --jwt-only")
        if a.installation_id is not None and not INSTALLATION_ID_RE.fullmatch(a.installation_id):
            raise SystemExit(f"--installation-id {a.installation_id!r}: expected a number")
        for name in a.repo:
            if not REPO_RE.fullmatch(name) or name in (".", ".."):
                raise SystemExit(f"--repo {name!r}: expected a repository name without the owner")
        if not re.match(r"https://|http://(localhost|127\.0\.0\.1)(:[0-9]+)?(/|$)", a.api_url):
            raise SystemExit(f"--api-url {a.api_url!r}: expected an https:// URL "
                             "(http:// only for localhost, in tests)")
        perms = None if a.all_permissions else (parse_permissions(a.permission)
                                                or dict(DEFAULT_PERMISSIONS))
        jwt_token = make_jwt(a.app_id, a.key)
    except SystemExit as e:
        print(e.code, file=sys.stderr)
        return 2

    if a.jwt_only:
        print(jwt_token)
        return 0

    body: dict = {}
    if a.repo:
        body["repositories"] = a.repo
    if perms is not None:
        body["permissions"] = perms
    try:
        out = request(a.api_url, f"/app/installations/{a.installation_id}/access_tokens",
                      jwt_token, body)
    except SystemExit as e:
        return int(e.code or 1)
    token = out.get("token") if isinstance(out, dict) else None
    if not isinstance(token, str) or not token:
        print("GitHub answered without a token: "
              f"{json.dumps(out)[:300] if isinstance(out, dict) else type(out).__name__}",
              file=sys.stderr)
        return 1
    print(token)
    repos = out.get("repositories")
    names = [r.get("full_name") for r in repos] if isinstance(repos, list) else "all granted"
    print(f"expires_at={out.get('expires_at')} repositories={names} "
          f"permissions={out.get('permissions')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
