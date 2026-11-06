"""rk_snapshot.py: no-progress snapshots of the tracker graph (B6.5, L2 §4.6).

    python3 -I rk_snapshot.py take --graph GRAPH.json [--out SNAP.json]
    python3 -I rk_snapshot.py compare OLD.json NEW.json

`take` records every node's state of a `tracker graph` JSON as reviewkit-snapshot/0:
{"format", "nodes": {id: {"state", "planned", "wrong", "deprecated"}}, "totals": {...}}.
Without --out it prints the snapshot; with --out it writes it and prints a reviewkit-report/0
envelope (check "snapshot-take") with the totals.

`compare` reads two snapshots (or two tracker graphs) and prints a reviewkit-report/0 with check
"snapshot" and the counts over planned nodes: progress (rank rose; open 0 < stated 1 < axioms 2
< proved 3), regressions (rank fell), wrong_new, deprecated_new, added, removed, no_progress
(progress == 0). Only nodes planned in both snapshots and marked wrong or deprecated in neither
are ranked; newly planned, removed, wrong or deprecated nodes are counted apart and never count
as progress (or as a regression).
Diagnostics: warning snapshot-no-progress, warning snapshot-regression (per node), info
snapshot-progress, snapshot-wrong-new, snapshot-deprecated-new, snapshot-added,
snapshot-removed (per node). tracker counts an opaque placeholder as proved; that is expected
(the placeholder census, not tracker, measures placeholders).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

SNAP_FORMAT = "reviewkit-snapshot/0"
RANK = {"open": 0, "stated": 1, "axioms": 2, "proved": 3}
STATES = set(RANK) | {"wrong"}


def from_graph(g, where: str) -> dict:
    nodes = {}
    for n in g["nodes"]:
        st = n.get("state", "open")
        if st not in STATES:
            raise rk.SetupError(f"{where}: node {n['id']} has unknown state {st!r}")
        nodes[n["id"]] = {"state": st, "planned": bool(n.get("planned")),
                          "wrong": bool(n.get("wrong")) or st == "wrong",
                          "deprecated": bool(n.get("deprecated"))}
    return {"format": SNAP_FORMAT, "nodes": nodes, "totals": totals(nodes)}


def totals(nodes: dict) -> dict:
    t = {"nodes": len(nodes), "planned": 0, "open": 0, "stated": 0, "axioms": 0, "proved": 0,
         "wrong": 0, "deprecated": 0}
    for v in nodes.values():
        if not v["planned"]:
            continue
        t["planned"] += 1
        t["wrong" if v["wrong"] else v["state"]] += 1
        if v["deprecated"]:
            t["deprecated"] += 1
    return t


def load_snapshot(path: str) -> dict:
    j = rk.load_json(path, "snapshot")
    if isinstance(j, dict) and j.get("format") == SNAP_FORMAT:
        if not isinstance(j.get("nodes"), dict):
            raise rk.SetupError(f"{path}: snapshot has no nodes table")
        for nid, v in j["nodes"].items():
            if not (isinstance(v, dict) and v.get("state") in STATES):
                raise rk.SetupError(f"{path}: node {nid} has no known state")
            for k in ("planned", "wrong", "deprecated"):
                v[k] = bool(v.get(k))
            v["wrong"] = v["wrong"] or v["state"] == "wrong"
        return j
    if isinstance(j, dict) and isinstance(j.get("nodes"), list):
        return from_graph(rk.load_graph(path), path)
    raise rk.SetupError(f"{path}: neither a {SNAP_FORMAT} snapshot nor a tracker graph")


def compare(old: dict, new: dict) -> rk.Report:
    rep = rk.Report("snapshot")
    on, nn = old["nodes"], new["nodes"]
    progress = regressions = wrong_new = deprecated_new = added = removed = 0
    for nid in sorted(set(on) | set(nn)):
        o, n = on.get(nid), nn.get(nid)
        op = bool(o and o["planned"])
        np_ = bool(n and n["planned"])
        if op and not np_:
            removed += 1
            rep.add("snapshot-removed", f"{nid} is no longer planned", "info", decl=nid)
            continue
        if np_ and not op:
            added += 1
            rep.add("snapshot-added", f"{nid} is newly planned ({n['state']})", "info", decl=nid)
            if n["wrong"]:
                wrong_new += 1
                rep.add("snapshot-wrong-new", f"{nid} is marked wrong", "info", decl=nid)
            continue
        if not np_:
            continue
        if n["deprecated"] and not o["deprecated"]:
            deprecated_new += 1
            rep.add("snapshot-deprecated-new", f"{nid} is newly deprecated", "info", decl=nid)
        if n["wrong"] and not o["wrong"]:
            wrong_new += 1
            rep.add("snapshot-wrong-new", f"{nid} is newly marked wrong", "info", decl=nid)
            continue
        if n["wrong"] or o["wrong"] or n["deprecated"] or o["deprecated"]:
            continue
        ro, rn = RANK[o["state"]], RANK[n["state"]]
        if rn > ro:
            progress += 1
            rep.add("snapshot-progress", f"{nid}: {o['state']} -> {n['state']}", "info", decl=nid)
        elif rn < ro:
            regressions += 1
            rep.add("snapshot-regression", f"{nid}: {o['state']} -> {n['state']}", "warning",
                    decl=nid)
    no_progress = progress == 0
    if no_progress:
        rep.add("snapshot-no-progress", "no planned node rose in state since the last snapshot "
                f"(added {added}, removed {removed}, newly wrong {wrong_new}, newly deprecated "
                f"{deprecated_new}, regressions {regressions})", "warning")
    rep.data.update({"progress": progress, "regressions": regressions, "wrong_new": wrong_new,
                     "deprecated_new": deprecated_new, "added": added, "removed": removed,
                     "no_progress": no_progress, "totals_old": old.get("totals"),
                     "totals_new": new.get("totals") or totals(nn)})
    return rep


def main(argv):
    if not argv or argv[0] not in ("take", "compare"):
        raise rk.SetupError("usage: rk_snapshot.py take --graph GRAPH.json [--out SNAP.json] | "
                            "compare OLD.json NEW.json")
    sub, rest = argv[0], argv[1:]
    if sub == "take":
        p = rk.ArgParser(prog="rk_snapshot.py take")
        p.add_argument("--graph", required=True)
        p.add_argument("--out")
        a = p.parse_args(rest)
        snap = from_graph(rk.load_graph(a.graph), a.graph)
        if not a.out:
            sys.stdout.write(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
            sys.exit(0)
        try:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
                f.write("\n")
        except OSError as e:
            raise rk.SetupError(f"cannot write {a.out}: {e}") from None
        rep = rk.Report("snapshot-take")
        rep.data.update({"out": a.out, "totals": snap["totals"]})
        return "", [rep]
    p = rk.ArgParser(prog="rk_snapshot.py compare")
    p.add_argument("old")
    p.add_argument("new")
    a = p.parse_args(rest)
    return "", [compare(load_snapshot(a.old), load_snapshot(a.new))]


if __name__ == "__main__":
    cmd = "snapshot " + sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("take", "compare") \
        else "snapshot"
    rk.run_main(cmd, main)
