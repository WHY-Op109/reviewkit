"""rk_extras.py: join the tracker graph with the extras file (reviewkit-extras/0, K8, D12).

    python3 -I rk_extras.py --config C [--root DIR] --graph GRAPH.json [--extras FILE]
                            [--closure CLOSURE.json] [--out CARDS.json]

GRAPH.json is `tracker graph --all` output. The extras file defaults to the config's
`extras.file`, relative to --root (default: the working directory). Checks (report "extras"):

  extras-format              the file is not TOML, or `format` is not reviewkit-extras/0
  extras-unknown-key         a key the format does not define (top level or in an entry)
  extras-type                a value of the wrong type
  extras-stale-id            an entry whose id is no node of the graph (a rename)
  extras-provider-missing    no provider, when extras.require_provider
  extras-provider-unknown    a provider not in extras.providers
  extras-key-outside-closure (warning) a `key = true` entry that is not in the closure report's
                             key list, when --closure is given

With --out, writes the cards (reviewkit-extras-join/0): one per planned node and per node that
has an extras entry, with the graph's fields and the entry's fields.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

EXTRAS_FORMAT = "reviewkit-extras/0"
JOIN_FORMAT = "reviewkit-extras-join/0"
ENTRY_KEYS = {"provider": "string", "key": "boolean", "chain": "boolean", "upstream": "string",
              "blueprint": "string", "hypotheses": "array of strings", "note": "string"}


def type_ok(kind: str, v) -> bool:
    if kind == "string":
        return isinstance(v, str)
    if kind == "boolean":
        return isinstance(v, bool)
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def closure_keys(path: str) -> set[str]:
    j = rk.load_json(path, "closure report")
    rep = None
    if isinstance(j, dict) and isinstance(j.get("reports"), list):
        rep = next((r for r in j["reports"] if isinstance(r, dict) and r.get("check") == "closure"),
                   None)
    elif isinstance(j, dict) and j.get("check") == "closure":
        rep = j
    if rep is None or not isinstance(rep.get("key_list"), list):
        raise rk.SetupError(f"{path}: no closure report with a key_list")
    out = set()
    for x in rep["key_list"]:
        if isinstance(x, dict) and isinstance(x.get("decl"), str):
            out.add(x["decl"])
    return out


def check_extras(rep: rk.Report, data, cfg: rk.Config, ids: set[str]) -> dict[str, dict]:
    """Check a decoded extras file; return the well-formed entries {id: entry}."""
    entries: dict[str, dict] = {}
    if data.get("format") != EXTRAS_FORMAT:
        rep.add("extras-format", f"format must be {EXTRAS_FORMAT}, found {data.get('format')!r}")
    for k in sorted(set(data) - {"format", "decl"}):
        rep.add("extras-unknown-key", f"unknown top-level key {k!r} (entries go under [decl.\"<id>\"])")
    decls = data.get("decl", {})
    if not isinstance(decls, dict):
        rep.add("extras-type", "`decl` must be a table of entries keyed by declaration id")
        return entries
    providers = cfg.get("extras", "providers")
    require = cfg.get("extras", "require_provider")
    for did, e in decls.items():
        if not isinstance(e, dict):
            rep.add("extras-type", f"entry {did} must be a table", decl=did)
            continue
        for k in sorted(set(e) - set(ENTRY_KEYS)):
            rep.add("extras-unknown-key", f"{did}: unknown key {k!r} (allowed: "
                    f"{', '.join(ENTRY_KEYS)})", decl=did)
        good = {}
        for k, kind in ENTRY_KEYS.items():
            if k in e:
                if type_ok(kind, e[k]):
                    good[k] = e[k]
                else:
                    rep.add("extras-type", f"{did}: {k} must be a {kind}", decl=did)
        if did not in ids:
            rep.add("extras-stale-id", f"{did} is no node of the tracker graph (renamed or "
                    "removed? the graph must be `tracker graph --all`)", decl=did)
        if "provider" not in e:
            if require:
                rep.add("extras-provider-missing", f"{did} names no provider", decl=did)
        elif "provider" in good and providers and good["provider"] not in providers:
            rep.add("extras-provider-unknown", f"{did}: provider {good['provider']!r} is not one "
                    f"of {', '.join(providers)}", decl=did)
        entries[did] = good
    return entries


def make_cards(g, entries: dict[str, dict]) -> list[dict]:
    nodes = g["nodes"]
    cards = []
    for n in nodes:
        nid = n["id"]
        if not n.get("planned") and nid not in entries:
            continue
        deps = []
        for d in n.get("deps", []) or []:
            if isinstance(d, int) and 0 <= d < len(nodes):
                deps.append(nodes[d]["id"])
        card = {"id": nid, "module": rk.module_name(g, n.get("module")), "kind": n.get("kind"),
                "state": "wrong" if n.get("wrong") else n.get("state"),
                "planned": bool(n.get("planned")), "desc": n.get("desc", ""),
                "source": n.get("source"), "deps": deps}
        if n.get("wrong"):
            card["wrong"] = n["wrong"]
        if n.get("deprecated"):
            card["deprecated"] = n["deprecated"]
        card.update(entries.get(nid, {}))
        cards.append(card)
    return cards


def main(argv):
    p = rk.ArgParser(prog="rk_extras.py", description=__doc__.split("\n")[0])
    p.add_argument("--config", required=True)
    p.add_argument("--root", default=".")
    p.add_argument("--graph", required=True)
    p.add_argument("--extras")
    p.add_argument("--closure")
    p.add_argument("--out")
    a = p.parse_args(argv)
    cfg = rk.load_config(a.config)
    g = rk.load_graph(a.graph)
    extras_path = a.extras or rk.under(a.root, cfg.need("extras", "file"))
    keys = closure_keys(a.closure) if a.closure else None
    rep = rk.Report("extras")
    rep.data["extras"] = extras_path
    rep.data["graph"] = a.graph
    data, err = rk.try_load_toml(extras_path)
    entries: dict[str, dict] = {}
    if data is None:
        rep.add("extras-format", f"{extras_path} is not valid TOML: {err}", file=extras_path)
    else:
        ids = {n["id"] for n in g["nodes"]}
        entries = check_extras(rep, data, cfg, ids)
        if keys is not None:
            for did, e in entries.items():
                if e.get("key") is True and did not in keys:
                    rep.add("extras-key-outside-closure", f"{did} is flagged key but is not in "
                            "the closure's key-definition list", "warning", decl=did)
    cards = make_cards(g, entries) if data is not None else []
    rep.data["entries"] = len(entries)
    rep.data["cards"] = len(cards)
    if a.out:
        try:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump({"format": JOIN_FORMAT, "cards": cards}, f, ensure_ascii=False, indent=1)
                f.write("\n")
        except OSError as e:
            raise rk.SetupError(f"cannot write {a.out}: {e}") from None
        rep.data["out"] = a.out
    return a.config, [rep]


if __name__ == "__main__":
    rk.run_main("extras", main)
