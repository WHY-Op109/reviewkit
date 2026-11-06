"""rk_owned.py: the owned-paths check (D9, D44; AGENTS.md ownership rule).

    python3 -I rk_owned.py --config C [--root DIR] (--changed-files FILE | --base REV --head REV)
                           [--author LOGIN] [--reviews REVIEWS.json --head-sha SHA]

Owned paths are the config's `owned.paths`: root-anchored entries, `/dir/` for a directory and
everything under it, `/file` for one exact path (a submodule is named without a trailing /).
Changed files come from FILE (one path, or one `git diff --name-status` line, per line) or from
`git diff --name-status -M -C <merge-base BASE HEAD> HEAD` in --root; both sides of a rename or
copy count. Diagnostics (report "owned-paths"):

  owned-path-changed       (error) a changed path under an owned path, without approval
  owned-path-approved      (info)  the same, when the approve-to-pass rule passes
  codeowners-missing       (error) an owned path that CODEOWNERS (owned.codeowners) does not
                                   assign to an owner (`@login` of owned.owners)
  codeowners-file-missing  (error) the CODEOWNERS file does not exist
  codeowners-unsupported   (error) a CODEOWNERS line this checker cannot interpret

Approval (FORMATS.md "Approval context"): the author is an owner, or an owner's latest
non-comment review is APPROVED on --head-sha.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

SKIP_DIRS = {".git", ".lake"}


def representative_paths(entry: str, root: str, co: rk.CodeOwners) -> list[str]:
    """Paths that stand for an owned entry when checking CODEOWNERS: for a directory, the
    directory plus "/x", every literal CODEOWNERS pattern under it, and the files on disk under
    it (outside .git and .lake); for a file, the file itself."""
    body = entry.strip("/")
    if not entry.endswith("/"):
        return [body]
    reps = [body + "/x"]
    for pat in co.literal_patterns():
        q = pat.strip("/")
        if q.startswith(body + "/"):
            reps.append(q + "/x" if pat.endswith("/") else q)
    top = os.path.join(root, body)
    if os.path.isdir(top):
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                reps.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
    return list(dict.fromkeys(reps))


def check_codeowners(rep: rk.Report, cfg: rk.Config, root: str) -> None:
    rel = cfg.get("owned", "codeowners")
    path = rk.under(root, rel)
    if not os.path.isfile(path):
        rep.add("codeowners-file-missing", f"the CODEOWNERS file {rel} does not exist", file=rel)
        return
    co = rk.CodeOwners(rk.read_text(path, "CODEOWNERS"))
    for e in co.errors:
        rep.add("codeowners-unsupported", f"{rel}: {e}", file=rel)
    handles = {"@" + o.lower() for o in cfg.get("owned", "owners")}
    for entry in cfg.get("owned", "paths"):
        bad = []
        for rp in representative_paths(entry, root, co):
            line, owners = co.owners_of(rp)
            if not any(o.lower() in handles for o in owners):
                bad.append((rp, line, owners))
        if bad:
            ex = "; ".join(
                f"{rp}: " + (f"line {ln} gives {' '.join(ow) or 'no owner'}" if ln else "no line matches")
                for rp, ln, ow in bad[:5])
            more = f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
            rep.add("codeowners-missing", f"owned path {entry} is not assigned to an owner in "
                    f"{rel}: {ex}{more}", file=entry)


def main(argv):
    p = rk.ArgParser(prog="rk_owned.py")
    p.add_argument("--config", required=True)
    p.add_argument("--root", default=".")
    rk.add_changed_args(p, required=True)
    rk.add_approval_args(p)
    a = p.parse_args(argv)
    cfg = rk.load_config(a.config)
    owners = cfg.need("owned", "owners")
    entries = cfg.need("owned", "paths")
    for e in entries:
        rk.check_owned_entry(e, cfg.path)
    changed = rk.changed_from_args(a, a.root) or []
    approval = rk.approval_passes(owners, a.author, a.reviews, a.head_sha)
    rep = rk.Report("owned-paths")
    hits = []
    for path in changed:
        owning = [e for e in entries if rk.owned_match(e, path)]
        if not owning:
            continue
        hits.append(path)
        if approval["passed"]:
            rep.add("owned-path-approved", f"{path} is under owned path {owning[0]}; approved: "
                    f"{approval['reason']}", "info", file=path)
        else:
            rep.add("owned-path-changed", f"{path} is under owned path {owning[0]} and needs an "
                    f"owner's approval on the head commit ({approval['reason']})", file=path)
    check_codeowners(rep, cfg, a.root)
    rep.data["changed"] = len(changed)
    rep.data["owned_changes"] = hits
    rep.data["approval"] = approval
    return a.config, [rep]


if __name__ == "__main__":
    rk.run_main("owned-paths", main)
