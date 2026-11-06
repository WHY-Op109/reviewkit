"""rk_faults.py: run reviewkit-policy-faults/0 fixtures against the Python checks.

    python3 -I rk_faults.py FILE.toml

Each [[case]] runs `<this python> -I <scripts dir>/<script> <args...>` with the working
directory set to the case's `dir` (relative to FILE's directory), parses its JSON output and
collects the diagnostics of all its reports. A fault passes when every `expect` is matched (code
and severity, and decl and file when given), every error is expected, and at least one error is
produced when an expect has severity error. A control passes when it produces no error that is
not expected. A setup failure (exit 2), output that is not a reviewkit-report/0 envelope with a
`reports` array, or an exit status that disagrees with the errors reported always fails the
case. Exit 1 if any case fails, 2 if FILE is unusable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

FORMAT = "reviewkit-policy-faults/0"
SCRIPTS = {"extras": "rk_extras.py"}
CASE_KEYS = {"name", "kind", "command", "dir", "args", "expect", "stdin"}
EXPECT_KEYS = {"code", "decl", "file", "severity"}
HERE = os.path.dirname(os.path.abspath(__file__))


def load_cases(path: str) -> list[dict]:
    t = rk.load_toml(path, "faults file")
    if t.get("format") != FORMAT:
        raise rk.SetupError(f"{path}: format must be {FORMAT}")
    extra = set(t) - {"format", "case"}
    if extra:
        raise rk.SetupError(f"{path}: unknown key(s) {', '.join(sorted(extra))}")
    cases = t.get("case", [])
    if not isinstance(cases, list) or not cases:
        raise rk.SetupError(f"{path}: no [[case]] tables")
    out = []
    for i, c in enumerate(cases):
        where = f"{path}: case {i + 1}"
        if not isinstance(c, dict):
            raise rk.SetupError(f"{where} is not a table")
        bad = set(c) - CASE_KEYS
        if bad:
            raise rk.SetupError(f"{where}: unknown key(s) {', '.join(sorted(bad))}")
        for k in ("name", "kind", "command"):
            if not isinstance(c.get(k), str):
                raise rk.SetupError(f"{where}: {k} must be a string")
        if c["kind"] not in ("fault", "control"):
            raise rk.SetupError(f"{where}: kind must be fault or control")
        args = c.get("args", [])
        if not (isinstance(args, list) and all(isinstance(x, str) for x in args)):
            raise rk.SetupError(f"{where}: args must be an array of strings")
        for k in ("dir", "stdin"):
            if k in c and not isinstance(c[k], str):
                raise rk.SetupError(f"{where}: {k} must be a string")
        exps = c.get("expect", [])
        if not isinstance(exps, list):
            raise rk.SetupError(f"{where}: expect must be an array of tables")
        norm = []
        for e in exps:
            if not isinstance(e, dict) or not isinstance(e.get("code"), str):
                raise rk.SetupError(f"{where}: every expect needs a code")
            bad = set(e) - EXPECT_KEYS
            if bad:
                raise rk.SetupError(f"{where}: unknown expect key(s) {', '.join(sorted(bad))}")
            sev = e.get("severity", "error")
            if sev not in rk.SEVERITIES:
                raise rk.SetupError(f"{where}: severity must be error, warning or info")
            norm.append({**e, "severity": sev})
        if c["kind"] == "fault" and not norm:
            raise rk.SetupError(f"{where}: a fault must name the diagnostic it expects")
        out.append({**c, "args": args, "expect": norm})
    return out


def matches(e: dict, d: dict) -> bool:
    if d.get("code") != e["code"] or d.get("severity") != e["severity"]:
        return False
    if "decl" in e and d.get("decl") != e["decl"]:
        return False
    if "file" in e and d.get("file") != e["file"]:
        return False
    return True


def describe(e: dict) -> str:
    s = e["code"]
    if "decl" in e:
        s += f" at {e['decl']}"
    if "file" in e:
        s += f" in {e['file']}"
    return s + f" ({e['severity']})"


def run_case(c: dict, base_dir: str) -> tuple[list[str], list[dict]]:
    script = SCRIPTS.get(c["command"])
    if script is None:
        return [f"setup error: unknown command {c['command']!r}"], []
    sp = os.path.join(HERE, script)
    if not os.path.isfile(sp):
        return [f"setup error: {script} does not exist"], []
    cwd = os.path.normpath(os.path.join(base_dir, c.get("dir", ".")))
    if not os.path.isdir(cwd):
        return [f"setup error: case directory {cwd} does not exist"], []
    try:
        r = subprocess.run([sys.executable, "-I", sp, *c["args"]], cwd=cwd,
                           input=c.get("stdin", "").encode("utf-8"), capture_output=True,
                           timeout=300, check=False)
    except subprocess.TimeoutExpired:
        return ["setup error: the check timed out"], []
    try:
        out = json.loads(r.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        err = r.stderr.decode("utf-8", "replace")[-600:]
        return [f"setup error: exit {r.returncode}, output is not JSON: {err}"], []
    if not isinstance(out, dict):
        return [f"setup error: exit {r.returncode}, output is not a JSON object"], []
    if r.returncode == 2 or "setup_error" in out:
        return [f"setup error: {out.get('setup_error', 'exit 2')}"], []
    if r.returncode not in (0, 1):
        return [f"setup error: exit {r.returncode}"], []
    if out.get("format") != rk.REPORT_FORMAT or not isinstance(out.get("reports"), list) \
            or not all(isinstance(x, dict) for x in out["reports"]):
        return [f"setup error: the output is not a {rk.REPORT_FORMAT} envelope with a reports "
                "array"], []
    diags = []
    for rep in out["reports"]:
        ds = rep.get("diagnostics", [])
        if not (isinstance(ds, list) and all(isinstance(d, dict) for d in ds)):
            return [f"setup error: report {rep.get('check')!r} has no diagnostics array"], []
        diags.extend(ds)
    errors = [d for d in diags if d.get("severity") == "error"]
    fails = []
    if (r.returncode == 1) != bool(errors):
        fails.append(f"exit status {r.returncode} disagrees with {len(errors)} error(s)")
    for e in c["expect"]:
        if not any(matches(e, d) for d in diags):
            fails.append(f"expected diagnostic not produced: {describe(e)}")
    for d in errors:
        if not any(matches(e, d) for e in c["expect"]):
            kind = "control produced an error" if c["kind"] == "control" else "unexpected error"
            fails.append(f"{kind}: {d.get('code')} {d.get('decl') or d.get('file') or ''}: "
                         f"{d.get('message')}")
    if c["kind"] == "fault" and any(e["severity"] == "error" for e in c["expect"]) and not errors:
        fails.append("fault produced no error")
    return fails, diags


def main(argv):
    if len(argv) != 1:
        raise rk.SetupError("usage: rk_faults.py FILE.toml")
    path = argv[0]
    cases = load_cases(path)
    base = os.path.dirname(os.path.abspath(path))
    results = []
    ok = True
    for c in cases:
        fails, diags = run_case(c, base)
        passed = not fails
        ok = ok and passed
        results.append({"case": c["name"], "kind": c["kind"], "command": c["command"],
                        "passed": passed, "failures": fails, "diagnostics": diags})
    nf = sum(1 for c in cases if c["kind"] == "fault")
    nc = len(cases) - nf
    npass = sum(1 for r in results if r["passed"])
    rk.print_json(rk.envelope("faults", path, ok, faults={
        "faults": nf, "controls": nc, "passed": npass, "failed": len(results) - npass,
        "results": results}))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except rk.SetupError as e:
        code = rk.emit_setup_error("faults", sys.argv[1] if len(sys.argv) > 1 else "", str(e))
    except Exception as e:  # noqa: BLE001 - a crash is a setup failure, not a failed case
        code = rk.emit_setup_error("faults", sys.argv[1] if len(sys.argv) > 1 else "",
                                   rk.crash_message(e))
    sys.exit(code)
