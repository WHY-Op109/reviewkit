"""rk_flagged.py: the D19 flagged-entries check (approve-to-pass, N7 §4.2).

    python3 -I rk_flagged.py --config C --base-root DIR --head-root DIR [--flags-root DIR]...
                             [--author LOGIN] [--reviews REVIEWS.json --head-sha SHA]

BASE-ROOT and HEAD-ROOT are checkouts of the PR's merge base and head. A declaration is flagged
when its entry in the extras file (config extras.file) of the BASE tree, or of any --flags-root
tree, sets any of flagged.flags to true. Pass the base branch's current tip as --flags-root: a
flag the owner set on the base branch after the PR branched then protects the node too (without
it, a PR that is not brought up to date is judged by its merge base's flags only). The check
compares the tracker plans (every *.toml under tracker.plans, recursively) and the extras file
of the BASE and HEAD trees. Diagnostics (report "flagged-entries"):

  flagged-node-removed     a flagged node's plan entry is gone from the head plans (removed,
                           renamed, or its plan's namespace changed)
  flagged-node-edited      a change to one of flagged.plan_fields of a flagged node, deletion
                           included; also a flagged node moved to another plan file, a flagged
                           node planned twice, or a plan entry added for a flagged declaration
  flagged-flag-changed     a flag value of any extras entry added, removed or changed, or a
                           flagged entry removed from the extras file
  flagged-entry-edited     a field other than the flags (provider, upstream, hypotheses,
                           blueprint, note, ...) of a flagged declaration's extras entry added,
                           removed or changed: the card the owner reviews is built from them
  flagged-approved         (info) any of the above when the approve-to-pass rule passes
  flagged-unreadable       a head plan or extras file that is not valid TOML (the change
                           cannot be judged)
  flagged-no-base-extras   (info) the base tree has no extras file (flags then come from the
                           --flags-root trees only, and every head flag counts as added)

All but flagged-approved are errors unless the approval passes. The report's "queue" lists
every flagged edit (decl, field, before, after): the owner's review queue.

Plan ids are resolved as tracker resolves them (Tracker/Plan.lean `resolveId`): an id starting
with `_root_.` is absolute; otherwise it is prefixed with the plan's `namespace` when set; else
it is taken as written. `deps` are compared as written.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

MISSING = None


def same_flag(bv, hv) -> bool:
    """Flag values agree: an absent flag means false; non-boolean values compare by type and
    value (1 is not true)."""
    nb = False if bv is MISSING else bv
    nh = False if hv is MISSING else hv
    return type(nb) is type(nh) and nb == nh


def plan_nodes(root: str, plans_rel: str, side: str, rep: rk.Report):
    """({absolute id: [(plan file relative to the plans dir, node table)]}, unreadable files)
    for one tree."""
    top = rk.under(root, plans_rel)
    out: dict[str, list[tuple[str, dict]]] = {}
    bad: set[str] = set()
    if not os.path.isdir(top):
        return out, bad
    files = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.endswith(".toml"):
                files.append(os.path.join(dirpath, fn))
    for f in sorted(files):
        rel = os.path.relpath(f, top).replace(os.sep, "/")
        data, err = rk.try_load_toml(f)
        if data is None:
            if side == "base":
                raise rk.SetupError(f"base plan {f} is not valid TOML: {err}")
            rep.add("flagged-unreadable", f"head plan {rel} is not valid TOML: {err}",
                    file=os.path.join(plans_rel, rel))
            bad.add(rel)
            continue
        ns = data.get("namespace")
        ns = ns if isinstance(ns, str) and ns else None
        nodes = data.get("node", [])
        if not isinstance(nodes, list):
            continue
        for n in nodes:
            if isinstance(n, dict) and isinstance(n.get("id"), str):
                out.setdefault(rk.resolve_plan_id(ns, n["id"]), []).append((rel, n))
    return out, bad


def extras_decls(path: str, side: str, rep: rk.Report) -> dict[str, dict] | None:
    if not os.path.isfile(path):
        return None
    data, err = rk.try_load_toml(path)
    if data is None:
        if side == "base":
            raise rk.SetupError(f"base extras file {path} is not valid TOML: {err}")
        rep.add("flagged-unreadable", f"head extras file is not valid TOML: {err}", file=path)
        return None
    decls = data.get("decl", {})
    if not isinstance(decls, dict):
        if side == "base":
            raise rk.SetupError(f"base extras file {path}: `decl` is not a table")
        decls = {}
    return {k: v for k, v in decls.items() if isinstance(v, dict)}


def main(argv):
    p = rk.ArgParser(prog="rk_flagged.py")
    p.add_argument("--config", required=True)
    p.add_argument("--base-root", required=True)
    p.add_argument("--head-root", required=True)
    p.add_argument("--flags-root", action="append", default=[],
                   help="another trusted tree (the base branch's tip) whose flags also count")
    rk.add_approval_args(p)
    a = p.parse_args(argv)
    cfg = rk.load_config(a.config)
    flags = cfg.need("flagged", "flags")
    fields = cfg.get("flagged", "plan_fields")
    plans_rel = cfg.need("tracker", "plans")
    extras_rel = cfg.need("extras", "file")
    owners = cfg.need("owned", "owners")
    for r in (a.base_root, a.head_root, *a.flags_root):
        if not os.path.isdir(r):
            raise rk.SetupError(f"{r} is not a directory")
    rep = rk.Report("flagged-entries")
    approval = rk.approval_passes(owners, a.author, a.reviews, a.head_sha)

    base_x = extras_decls(os.path.join(a.base_root, extras_rel), "base", rep)
    head_path = os.path.join(a.head_root, extras_rel)
    head_x = extras_decls(head_path, "head", rep)
    flag_trees = [x for x in (extras_decls(os.path.join(r, extras_rel), "base", rep)
                              for r in a.flags_root) if x is not None]
    if base_x is None:
        rep.add("flagged-no-base-extras", f"the base tree has no {extras_rel}: "
                + ("flags come from the --flags-root trees only" if flag_trees
                   else "nothing is flagged"), "info", file=extras_rel)
        base_x = {}
    head_unreadable = head_x is None and os.path.isfile(head_path)
    if head_x is None:
        head_x = {}
    flagged_set = set()
    for tree in (base_x, *flag_trees):
        flagged_set |= {d for d, e in tree.items() if any(e.get(f) is True for f in flags)}
    flagged = sorted(flagged_set)
    rep.data["flagged_from_base"] = sorted(
        d for d, e in base_x.items() if any(e.get(f) is True for f in flags))

    queue: list[dict] = []

    def edit(code: str, decl: str, field: str, before, after, message: str, file: str | None):
        queue.append({"code": code, "decl": decl, "field": field, "before": before,
                      "after": after, **({"file": file} if file else {})})
        if approval["passed"]:
            rep.add("flagged-approved", f"{message} (approved: {approval['reason']})", "info",
                    decl=decl, file=file)
        else:
            rep.add(code, f"{message}; needs the owner's approval on the head commit "
                    f"({approval['reason']})", decl=decl, file=file)

    # extras flags: any change for any entry; removal of a flagged entry
    if not head_unreadable:
        for did in sorted(set(base_x) | set(head_x)):
            b, h = base_x.get(did), head_x.get(did)
            if b is not None and h is None:
                if did in flagged:
                    edit("flagged-flag-changed", did, "entry", {f: b[f] for f in flags if f in b},
                         None, f"flagged extras entry {did} removed", extras_rel)
                    continue
            for f in flags:
                bv = (b or {}).get(f, MISSING)
                hv = (h or {}).get(f, MISSING)
                if not same_flag(bv, hv):
                    edit("flagged-flag-changed", did, f, bv, hv,
                         f"extras flag {f} of {did} changed: {bv!r} -> {hv!r}", extras_rel)
            if did in flagged and h is not None:
                for k in sorted((set(b or {}) | set(h)) - set(flags)):
                    bv, hv = (b or {}).get(k, MISSING), h.get(k, MISSING)
                    if not (type(bv) is type(hv) and bv == hv):
                        what = ("deleted" if hv is MISSING
                                else "added" if bv is MISSING else "changed")
                        edit("flagged-entry-edited", did, k, bv, hv,
                             f"field {k} of the flagged extras entry {did} {what}: "
                             f"{bv!r} -> {hv!r}", extras_rel)

    # plan entries of flagged declarations
    base_p, _ = plan_nodes(a.base_root, plans_rel, "base", rep)
    head_p, head_bad = plan_nodes(a.head_root, plans_rel, "head", rep)
    for did in flagged:
        bs, hs = base_p.get(did, []), head_p.get(did, [])
        if bs and not hs and bs[0][0] in head_bad:
            continue  # its plan file does not parse: flagged-unreadable already fails the check
        if bs and not hs:
            edit("flagged-node-removed", did, "node", bs[0][0], None,
                 f"flagged node {did} is gone from the plans (was in {bs[0][0]}; removed, "
                 "renamed or re-namespaced)", os.path.join(plans_rel, bs[0][0]))
            continue
        if not hs:
            continue
        if len(hs) > 1:
            edit("flagged-node-edited", did, "id", None, [f for f, _ in hs],
                 f"flagged node {did} is planned {len(hs)} times in the head plans",
                 os.path.join(plans_rel, hs[1][0]))
        hfile, hn = hs[0]
        bfile, bn = bs[0] if bs else (None, {})
        if bs and bfile != hfile:
            edit("flagged-node-edited", did, "module", bfile, hfile,
                 f"flagged node {did} moved from plan {bfile} to {hfile}",
                 os.path.join(plans_rel, hfile))
        for f in fields:
            bv, hv = bn.get(f, MISSING), hn.get(f, MISSING)
            if bv != hv:
                what = "deleted" if hv is MISSING else ("added" if bv is MISSING else "changed")
                edit("flagged-node-edited", did, f, bv, hv,
                     f"field {f} of flagged node {did} {what}: {bv!r} -> {hv!r}",
                     os.path.join(plans_rel, hfile))

    rep.data["flagged"] = flagged
    rep.data["queue"] = queue
    rep.data["approval"] = approval
    return a.config, [rep]


if __name__ == "__main__":
    rk.run_main("flagged-entries", main)
