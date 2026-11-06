"""Shared helpers for the kit's Python checks (data format v0, FORMATS.md).

Standard library only; runs on Python 3.11+. Every check prints one JSON object on stdout in
the `reviewkit-report/0` envelope that `lake exe review` uses:

    {"format": "reviewkit-report/0", "command": <cmd>, "config": <path>, "ok": bool,
     "reports": [{"check", "ok", "errors", "warnings", "diagnostics": [...], ...data}]}

Exit status: 0 no errors, 1 errors, 2 setup failure (the envelope then has "setup_error").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import traceback
from typing import Any

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

# The config tables the Python checks read, with their keys, types and defaults. Other tables
# (read by `lake exe review`) are passed through unchecked. An unknown key inside one of these
# tables is a setup error, so a misspelt key cannot become a silent default.
_STR, _BOOL, _STRLIST = "string", "boolean", "array of strings"
CONFIG_TABLES: dict[str, dict[str, tuple[str, Any]]] = {
    "extras": {"file": (_STR, None), "providers": (_STRLIST, []),
               "require_provider": (_BOOL, False)},
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


def load_config(path: str) -> Config:
    raw = load_toml(path, "config")
    fmt = raw.get("format")
    if fmt != CONFIG_FORMAT:
        raise SetupError(f"{path}: format must be {CONFIG_FORMAT}, found {fmt!r}")
    return Config(path, raw)


def under(root: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


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

