"""Shared helpers for the review kit's policy checks (data format v0, reviewkit/FORMATS.md).

Standard library only; runs on Python 3.11+. Every check prints one JSON object on stdout in
the `reviewkit-report/0` envelope that `lake exe review` uses:

    {"format": "reviewkit-report/0", "command": <cmd>, "config": <path>, "ok": bool,
     "reports": [{"check", "ok", "errors", "warnings", "diagnostics": [...], ...data}]}

Exit status: 0 no errors, 1 errors, 2 setup failure (the envelope then has "setup_error").

Nothing here is specific to one project (D35): every project fact comes from the config file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import traceback
from datetime import datetime, timezone
from typing import Any, Iterable

REPORT_FORMAT = "reviewkit-report/0"
CONFIG_FORMAT = "reviewkit-config/0"
SEVERITIES = ("error", "warning", "info")


class SetupError(Exception):
    """A setup failure: bad arguments, unreadable input, bad configuration. Exit status 2."""


# --- diagnostics and reports --------------------------------------------------------------------


class Report:
    def __init__(self, check: str):
        self.check = check
        self.diags: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}

    def add(self, code: str, message: str, severity: str = "error", *, decl: str | None = None,
            module: str | None = None, path: list[str] | None = None,
            file: str | None = None) -> dict[str, Any]:
        assert severity in SEVERITIES, severity
        d: dict[str, Any] = {"check": self.check, "code": code, "severity": severity,
                             "message": message}
        if decl is not None:
            d["decl"] = decl
        if module is not None:
            d["module"] = module
        if path:
            d["path"] = list(path)
        if file is not None:
            d["file"] = file
        self.diags.append(d)
        return d

    @property
    def errors(self) -> int:
        return sum(1 for d in self.diags if d["severity"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for d in self.diags if d["severity"] == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.check, "ok": self.ok, "errors": self.errors,
                               "warnings": self.warnings, "diagnostics": self.diags}
        for k, v in self.data.items():
            if k not in out:
                out[k] = v
        return out


def envelope(command: str, config: str, ok: bool, **body: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"format": REPORT_FORMAT, "command": command, "config": config,
                           "ok": ok}
    out.update(body)
    return out


def print_json(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    sys.stdout.flush()


def emit_reports(command: str, config: str, reports: list[Report]) -> int:
    ok = all(r.ok for r in reports)
    print_json(envelope(command, config, ok, reports=[r.to_json() for r in reports]))
    return 0 if ok else 1


def emit_setup_error(command: str, config: str, message: str) -> int:
    print_json(envelope(command, config, False, setup_error=message))
    return 2


def run_main(command: str, main) -> None:
    """Run `main(argv) -> (config_path, [Report])`; turn SetupError, and any other exception
    (a crash: the input could not be judged), into exit 2 with a setup_error envelope."""
    config = ""
    argv = sys.argv[1:]
    # best effort: name the config in a setup-error envelope too
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            config = argv[i + 1]
        elif a.startswith("--config="):
            config = a.split("=", 1)[1]
    try:
        config, reports = main(argv)
    except SetupError as e:
        sys.exit(emit_setup_error(command, config, str(e)))
    except Exception as e:  # noqa: BLE001 - a crash must not read as "errors found" (exit 1)
        sys.exit(emit_setup_error(command, config, crash_message(e)))
    sys.exit(emit_reports(command, config, reports))


def crash_message(e: BaseException) -> str:
    """A one-line description of an unexpected exception, with its innermost frame."""
    tb = traceback.extract_tb(e.__traceback__)
    where = f" at {os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else ""
    return f"internal error{where}: {type(e).__name__}: {e}"


class ArgParser(argparse.ArgumentParser):
    """argparse that raises SetupError instead of printing usage and exiting."""

    def error(self, message: str):  # type: ignore[override]
        raise SetupError(f"{self.prog}: {message}")

    def exit(self, status: int = 0, message: str | None = None):  # type: ignore[override]
        if status:
            raise SetupError(message or f"{self.prog}: exit {status}")
        # --help: argparse already printed usage on stdout
        sys.exit(0)


# --- reading files ------------------------------------------------------------------------------


def read_text(path: str, what: str = "file") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise SetupError(f"cannot read {what} {path}: {e.strerror or e}") from None
    except UnicodeDecodeError as e:
        raise SetupError(f"{what} {path} is not UTF-8: {e}") from None


def load_json(path: str, what: str = "JSON file") -> Any:
    text = read_text(path, what)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SetupError(f"{what} {path} is not valid JSON: {e}") from None


def load_toml(path: str, what: str = "TOML file") -> dict[str, Any]:
    """Load a TOML file; an unreadable or unparsable file is a setup failure."""
    text = read_text(path, what)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SetupError(f"{what} {path} is not valid TOML: {e}") from None


def try_load_toml(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load a TOML file: (value, None), or (None, reason) if it does not parse.
    A missing or unreadable file raises SetupError."""
    text = read_text(path, "TOML file")
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError as e:
        return None, str(e)


# --- configuration ------------------------------------------------------------------------------

# The v0 tables the Python checks read, with their keys, types and defaults. Other tables
# (A1's, read by `lake exe review`) are passed through unchecked. A key not listed here inside
# one of these tables is a setup error (a misspelt policy key must not become a silent default).
_STR, _BOOL, _STRLIST = "string", "boolean", "array of strings"
CONFIG_TABLES: dict[str, dict[str, tuple[str, Any]]] = {
    "evidence": {"dir": (_STR, "evidence")},
    "attestations": {"file": (_STR, None)},
    "extras": {"file": (_STR, None), "providers": (_STRLIST, []),
               "require_provider": (_BOOL, False)},
    "tracker": {"plans": (_STR, None), "roots": (_STRLIST, [])},
    "owned": {"owners": (_STRLIST, []), "paths": (_STRLIST, []),
              "codeowners": (_STR, ".github/CODEOWNERS")},
    "flagged": {"flags": (_STRLIST, []),
                "plan_fields": (_STRLIST, ["desc", "description", "source", "kind", "deps",
                                           "wrong", "deprecated"])},
    "pin_policy": {"package": (_STR, None), "lakefile": (_STR, "lakefile.toml"),
                   "manifest": (_STR, "lake-manifest.json"),
                   "toolchain": (_STR, "lean-toolchain"), "extra_toolchains": (_STRLIST, [])},
}


def _type_ok(kind: str, v: Any) -> bool:
    if kind == _STR:
        return isinstance(v, str)
    if kind == _BOOL:
        return isinstance(v, bool)
    if kind == _STRLIST:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


class Config:
    def __init__(self, path: str, raw: dict[str, Any]):
        self.path = path
        self.raw = raw
        self.tables: dict[str, dict[str, Any]] = {}
        for tname, spec in CONFIG_TABLES.items():
            t = raw.get(tname, {})
            if not isinstance(t, dict):
                raise SetupError(f"{path}: [{tname}] must be a table")
            unknown = sorted(set(t) - set(spec))
            if unknown:
                raise SetupError(f"{path}: unknown key(s) in [{tname}]: {', '.join(unknown)}")
            vals: dict[str, Any] = {}
            for k, (kind, default) in spec.items():
                if k in t:
                    if not _type_ok(kind, t[k]):
                        raise SetupError(f"{path}: {tname}.{k} must be a {kind}")
                    vals[k] = t[k]
                else:
                    vals[k] = list(default) if isinstance(default, list) else default
            self.tables[tname] = vals

    def get(self, table: str, key: str) -> Any:
        return self.tables[table][key]

    def need(self, table: str, key: str) -> Any:
        v = self.tables[table][key]
        if v is None or v == []:
            raise SetupError(f"{self.path}: {table}.{key} is required for this check")
        return v

    def upstream(self, package: str) -> dict[str, Any]:
        """`upstream.<package>`; the key is matched exactly, else case-insensitively (TOML keys
        are often lower-case while Lake package names are not)."""
        up = self.raw.get("upstream", {})
        if not isinstance(up, dict):
            raise SetupError(f"{self.path}: [upstream] must be a table")
        entry = up.get(package)
        if entry is None:
            matches = [k for k in up if k.lower() == package.lower() and isinstance(up[k], dict)]
            if len(matches) == 1:
                entry = up[matches[0]]
        if not isinstance(entry, dict):
            raise SetupError(f"{self.path}: no table upstream.{package} (rev, commit)")
        for k in ("rev", "commit"):
            if not isinstance(entry.get(k), str):
                raise SetupError(f"{self.path}: upstream.{package}.{k} must be a string")
        return entry


def load_config(path: str) -> Config:
    raw = load_toml(path, "config")
    fmt = raw.get("format")
    if fmt != CONFIG_FORMAT:
        raise SetupError(f"{path}: format must be {CONFIG_FORMAT}, found {fmt!r}")
    return Config(path, raw)


def under(root: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


# --- approval context (FORMATS.md "Approval context") -------------------------------------------


def _login_eq(a: str, b: str) -> bool:
    # GitHub logins are case-insensitive.
    return a.lower() == b.lower()


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _review_time(v: Any, where: str) -> datetime | None:
    """`submitted_at` as an aware datetime (naive times are taken as UTC); None if absent."""
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise SetupError(f"{where}: submitted_at is not a string")
    try:
        t = datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith(("Z", "z")) else v)
    except ValueError:
        raise SetupError(f"{where}: submitted_at {v!r} is not an ISO 8601 time") from None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def approval_passes(owners: list[str], author: str | None, reviews_path: str | None,
                    head_sha: str | None) -> dict[str, Any]:
    """Decide the approve-to-pass rule. Returns {"passed", "by", "reason", "considered"}.

    The PR passes if its author is an owner, or if some owner's latest non-comment review is
    APPROVED on the head commit. Comment-only (COMMENTED) and PENDING reviews are skipped when
    finding the latest review; a later CHANGES_REQUESTED or DISMISSED review voids an earlier
    approval. Reviews are ordered by `submitted_at` (ISO 8601, compared as instants; a review
    without one sorts first), then by position. A review whose `user` is null or has no login
    (a deleted account) is skipped: it cannot be an owner's.
    """
    result: dict[str, Any] = {"passed": False, "by": None, "reason": "no approval context"}
    if author and any(_login_eq(author, o) for o in owners):
        return {"passed": True, "by": author, "reason": f"the PR author {author} is an owner"}
    if reviews_path is None:
        if author:
            result["reason"] = f"the PR author {author} is not an owner and no reviews were given"
        return result
    if not head_sha:
        raise SetupError("--reviews needs --head-sha")
    reviews = load_json(reviews_path, "reviews file")
    if isinstance(reviews, dict) and isinstance(reviews.get("reviews"), list):
        reviews = reviews["reviews"]
    if not isinstance(reviews, list):
        raise SetupError(f"reviews file {reviews_path} must be a JSON array of reviews")
    indexed = []
    for i, r in enumerate(reviews):
        if not isinstance(r, dict):
            raise SetupError(f"reviews file {reviews_path}: entry {i} is not an object")
        user = r.get("user")
        state = r.get("state")
        if not isinstance(state, str):
            raise SetupError(f"reviews file {reviews_path}: entry {i} has no state")
        if user is not None and not isinstance(user, dict):
            raise SetupError(f"reviews file {reviews_path}: entry {i}: user is not an object")
        login = (user or {}).get("login")
        if not isinstance(login, str) or not login:
            continue  # a deleted account: never an owner
        ts = _review_time(r.get("submitted_at"), f"reviews file {reviews_path}: entry {i}")
        indexed.append(((ts is not None, ts or _EPOCH, i), login, state.upper(),
                        r.get("commit_id")))
    indexed.sort(key=lambda t: t[0])
    latest: dict[str, tuple[str, Any]] = {}
    for _, login, state, commit in indexed:
        if state in ("COMMENTED", "PENDING"):
            continue
        latest[login.lower()] = (state, commit)
    considered = []
    for login_l, (state, commit) in sorted(latest.items()):
        is_owner = any(_login_eq(login_l, o) for o in owners)
        considered.append({"login": login_l, "state": state, "commit_id": commit,
                           "owner": is_owner})
        if is_owner and state == "APPROVED" and commit == head_sha:
            return {"passed": True, "by": login_l,
                    "reason": f"owner {login_l} approved head commit {head_sha}",
                    "considered": considered}
    reasons = []
    for c in considered:
        if not c["owner"]:
            reasons.append(f"{c['login']} is not an owner")
        elif c["state"] != "APPROVED":
            reasons.append(f"owner {c['login']}'s latest review is {c['state']}")
        else:
            reasons.append(f"owner {c['login']} approved {c['commit_id']}, not the head commit")
    return {"passed": False, "by": None,
            "reason": "; ".join(reasons) or "no qualifying review",
            "considered": considered}


def add_approval_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--author", help="PR author login")
    p.add_argument("--reviews", help="GitHub pulls/N/reviews JSON array")
    p.add_argument("--head-sha", help="the PR's head commit (required with --reviews)")


# --- owned paths (restricted CODEOWNERS syntax) --------------------------------------------------


def check_owned_entry(entry: str, cfg_path: str) -> None:
    if not entry.startswith("/") or entry == "/":
        raise SetupError(f"{cfg_path}: owned path {entry!r} must be root-anchored (start with /)")
    if any(c in entry for c in "*?[]\\") or "//" in entry:
        raise SetupError(f"{cfg_path}: owned path {entry!r}: globs and escapes are not allowed")


def owned_match(entry: str, path: str) -> bool:
    """Restricted owned-path match: `/dir/` covers dir and everything under it; `/file` is exact."""
    path = path.strip("/")
    body = entry.strip("/")
    if entry.endswith("/"):
        return path == body or path.startswith(body + "/")
    return path == body


# --- CODEOWNERS (GitHub semantics, gitignore-style patterns; the last matching line wins) ---------


def _glob_to_regex(pat: str) -> str:
    out = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def codeowners_regex(pattern: str) -> re.Pattern[str]:
    """Compile one CODEOWNERS pattern to a regex over repository-relative paths (no leading /).

    As in gitignore: a pattern with a slash at the start or in the middle is anchored at the
    root, otherwise it matches at any depth; a trailing slash matches a directory's contents
    only; a pattern that names a directory also matches everything under it. One exception,
    GitHub's documented one: a last segment with a single-level wildcard (`*` or `?`, not
    `**`) and no trailing slash matches entries of that level only, so `docs/*` matches
    docs/a.md but not docs/sub/b.md (a bare `*` or `*.js` still matches at any depth)."""
    dir_only = pattern.endswith("/")
    p = pattern.rstrip("/") if dir_only else pattern
    anchored = p.startswith("/") or "/" in p
    p = p.lstrip("/")
    if p.endswith("/**"):
        body = _glob_to_regex(p[:-3]) + "/.*"
        tail = ""
    else:
        body = _glob_to_regex(p)
        last = p.rsplit("/", 1)[-1]
        if dir_only:
            tail = "/.*"
        elif ("*" in last or "?" in last) and "**" not in last:
            tail = ""
        else:
            tail = "(?:/.*)?"
    prefix = "" if anchored or body.startswith("(?:.*/)?") else "(?:.*/)?"
    return re.compile("^" + prefix + body + tail + "$")


class CodeOwners:
    def __init__(self, text: str):
        self.rules: list[tuple[int, str, re.Pattern[str], list[str]]] = []
        self.errors: list[str] = []
        for n, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # an unescaped # starts a comment
            s = re.split(r"(?<!\\)#", s, maxsplit=1)[0].strip()
            parts = s.split()
            if not parts:
                continue
            pat, owners = parts[0], parts[1:]
            if pat.startswith("!") or "[" in pat:
                self.errors.append(f"line {n}: unsupported CODEOWNERS pattern {pat!r}")
                continue
            try:
                rx = codeowners_regex(pat)
            except re.error as e:
                self.errors.append(f"line {n}: bad pattern {pat!r}: {e}")
                continue
            self.rules.append((n, pat, rx, owners))

    def owners_of(self, path: str) -> tuple[int | None, list[str]]:
        """(line, owners) of the last matching line, or (None, []) if none matches."""
        path = path.strip("/")
        hit: tuple[int | None, list[str]] = (None, [])
        for n, _, rx, owners in self.rules:
            if rx.match(path):
                hit = (n, owners)
        return hit

    def literal_patterns(self) -> list[str]:
        return [pat for _, pat, _, _ in self.rules if not any(c in pat for c in "*?")]


# --- changed files ------------------------------------------------------------------------------


def _unquote_git(p: str) -> str:
    """Undo git's C-style quoting of a path ("a\\tb", octal escapes)."""
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        body = p[1:-1]
        raw = bytearray()
        i = 0
        simple = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "v": 11}
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in simple:
                    raw.append(simple[nxt])
                    i += 2
                    continue
                if re.match(r"[0-7]{3}", body[i + 1:i + 4]):
                    raw.append(int(body[i + 1:i + 4], 8))
                    i += 4
                    continue
            raw.extend(c.encode("utf-8"))
            i += 1
        return raw.decode("utf-8", errors="replace")
    return p


def parse_changed_lines(text: str) -> list[str]:
    """Paths from a changed-files list: one path per line, or `git diff --name-status` lines
    (`M<TAB>path`, `R100<TAB>old<TAB>new`; both sides of a rename or copy count)."""
    paths: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            fields = line.split("\t")
            status, rest = fields[0].strip(), fields[1:]
            if not re.fullmatch(r"[ACDMRTUXB][0-9]*", status):
                raise SetupError(f"changed-files line has an unknown status: {line!r}")
            paths.extend(_unquote_git(f) for f in rest if f)
        else:
            paths.append(_unquote_git(line.strip()))
    return _dedupe(p.strip("/") for p in paths)


def _dedupe(xs: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def git(root: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, check=False)
    except OSError as e:
        raise SetupError(f"cannot run git: {e}") from None
    if r.returncode != 0:
        raise SetupError(f"git {' '.join(args)} failed in {root}: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")


def git_changed(root: str, base: str, head: str) -> list[str]:
    """Paths changed between the merge base of BASE and HEAD and HEAD, both sides of renames
    and copies; a submodule pointer change appears as the submodule's path."""
    mb = git(root, "merge-base", base, head).strip()
    out = git(root, "diff", "--name-status", "-z", "-M", "-C", "--no-ext-diff",
              "--ignore-submodules=none", mb, head)
    toks = out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(toks):
        st = toks[i]
        if not st:
            i += 1
            continue
        if st[0] in "RC":
            paths.extend(toks[i + 1:i + 3])
            i += 3
        else:
            paths.append(toks[i + 1] if i + 1 < len(toks) else "")
            i += 2
    return _dedupe(p.strip("/") for p in paths)


def add_changed_args(p: argparse.ArgumentParser, required: bool) -> None:
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument("--changed-files", help="file with one path or name-status line per line")
    g.add_argument("--base", help="base revision (the diff starts at its merge base with --head)")
    p.add_argument("--head", help="head revision (with --base)")


def changed_from_args(args: argparse.Namespace, root: str) -> list[str] | None:
    if getattr(args, "changed_files", None):
        if args.head:
            raise SetupError("--head goes with --base, not --changed-files")
        return parse_changed_lines(read_text(args.changed_files, "changed-files list"))
    if getattr(args, "base", None):
        if not args.head:
            raise SetupError("--base needs --head")
        return git_changed(root, args.base, args.head)
    if getattr(args, "head", None):
        raise SetupError("--head needs --base")
    return None


# --- tracker graph ------------------------------------------------------------------------------


def load_graph(path: str) -> dict[str, Any]:
    g = load_json(path, "tracker graph")
    if not (isinstance(g, dict) and isinstance(g.get("nodes"), list)
            and isinstance(g.get("modules"), list)):
        raise SetupError(f"{path}: not a tracker graph (needs \"modules\" and \"nodes\" arrays)")
    for i, n in enumerate(g["nodes"]):
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            raise SetupError(f"{path}: node {i} has no string id")
    return g


def module_name(g: dict[str, Any], idx: Any) -> str | None:
    if isinstance(idx, int) and 0 <= idx < len(g["modules"]):
        m = g["modules"][idx]
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            return m["name"]
    return None


def resolve_plan_id(namespace: str | None, raw: str) -> str:
    """tracker's `resolveId`: `_root_.` forces an absolute id; otherwise the id is relative to
    the module plan's namespace when one is set (always prefixed, as Lean resolves a name
    declared inside `namespace`); else as written."""
    if raw.startswith("_root_."):
        return raw[len("_root_."):]
    if namespace:
        return namespace + "." + raw
    return raw
