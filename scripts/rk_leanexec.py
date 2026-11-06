"""rk_leanexec.py: Lean code that runs while CI builds or imports a PR needs the owner's approval.

    python3 -I rk_leanexec.py --config C [--root DIR] (--changed-files FILE | --base REV --head REV)
                              [--author LOGIN] [--reviews REVIEWS.json --head-sha SHA]

`lake build` elaborates a PR's Lean files, and `review` and `tracker` import them with
initialisers enabled, all with the CI runner's rights; taking the checker from the base revision
does not stop such code from faking a verdict (for example by rewriting the checker's files
during the build). This check reads text only, nothing is built: a changed `.lean` file whose
new version (read from the checked-out head tree at --root) contains a construct that runs code
at build or import time is flagged, and so is any changed `lakefile.lean`. Approve-to-pass, with
the same approval rule and owners as owned-paths (`owned.owners`).

Heuristic [J]: it covers the entry points (initialisers, `#eval` and `run_*` commands,
elaborators, macros, simprocs, native and unsafe code, meta attributes), line by line, skipping
whole-line `--` comments; a word such as "macro" inside a doc comment is also flagged (a false
positive the owner approves). A change to a helper in another file that an already approved
entry point calls is not seen.

Report "lean-exec": lean-exec-changed (error), lean-exec-approved (info).
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

KINDS = [
    ("initialiser", r"\b(?:builtin_)?initialize\b"),
    ("#eval", r"#eval"),
    ("run command or tactic", r"\brun_(?:cmd|elab|meta|tac)\b"),
    ("elaborator, macro or simproc",
     r"\b(?:elab|elab_rules|macro|macro_rules|simproc|dsimproc|simproc_decl|dsimproc_decl)\b"),
    ("native or unsafe code",
     r"\b(?:implemented_by|extern|unsafe|native_decide|ofReduceBool|ofReduceNat)\b|\+native\b"),
    ("meta attribute",
     r"(?:@\[|\battribute\s*\[)[^\]]*\b(?:init|builtin_\w+|command_elab|term_elab|tactic|macro"
     r"|delab|app_delab|env_linter|code_action_provider|hole_code_action|command_code_action"
     r"|widget_module|server_rpc_method)\b"),
]
KINDS_RE = [(k, re.compile(r)) for k, r in KINDS]


def hits(text: str) -> list[tuple[int, list[str], str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("--"):  # a whole-line comment never holds code
            continue
        kinds = [k for k, rx in KINDS_RE if rx.search(line)]
        if kinds:
            out.append((n, kinds, line.strip()))
    return out


def main(argv):
    p = rk.ArgParser(prog="rk_leanexec.py")
    p.add_argument("--config", required=True)
    p.add_argument("--root", default=".")
    rk.add_changed_args(p, required=True)
    rk.add_approval_args(p)
    a = p.parse_args(argv)
    cfg = rk.load_config(a.config)
    owners = cfg.need("owned", "owners")
    approval = rk.approval_passes(owners, a.author, a.reviews, a.head_sha)
    changed = rk.changed_from_args(a, a.root) or []
    rep = rk.Report("lean-exec")
    flagged = []
    for path in changed:
        if not path.endswith(".lean"):
            continue
        full = rk.under(a.root, path)
        if not os.path.isfile(full):  # deleted, or only the old side of a rename
            continue
        found = hits(rk.read_text(full, path))
        if os.path.basename(path) == "lakefile.lean":
            found.insert(0, (1, ["Lake configuration (Lake prefers lakefile.lean to lakefile.toml)"], ""))
        if not found:
            continue
        flagged.append(path)
        kinds = sorted({k for _, ks, _ in found for k in ks})
        lines = "; ".join(f"line {n}: {t[:80]}" for n, _, t in found[:3] if t)
        more = f" (and {len(found) - 3} more lines)" if len(found) > 3 else ""
        what = (f"{path} changed and contains code that runs at build or import time "
                f"({', '.join(kinds)})" + (f": {lines}{more}" if lines else ""))
        if approval["passed"]:
            rep.add("lean-exec-approved", f"{what}; approved: {approval['reason']}", "info", file=path)
        else:
            rep.add("lean-exec-changed", f"{what}; needs an owner's approval on the head commit "
                    f"({approval['reason']})", file=path)
    rep.data["changed"] = len(changed)
    rep.data["flagged"] = flagged
    rep.data["approval"] = approval
    return a.config, [rep]


if __name__ == "__main__":
    rk.run_main("lean-exec", main)
