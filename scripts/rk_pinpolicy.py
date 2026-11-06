"""rk_pinpolicy.py: the D44 pin policy (lakefile, manifest and toolchain agree; D26, D43/F18).

    python3 -I rk_pinpolicy.py --config C [--root DIR] [--upstream-dir DIR | --fetch]
                               [--changed-files FILE | --base REV --head REV]
                               [--head-config C2]
                               [--author LOGIN] [--reviews REVIEWS.json --head-sha SHA]

For the package P = pin_policy.package and its pin upstream.<P> = {rev, commit} in the config:

 (a) pin-lakefile-shadowed         another Lake configuration file (lakefile.lean next to
                                   lakefile.toml) sits beside the configured lakefile: Lake
                                   prefers lakefile.lean, so the checked file would not be the
                                   one Lake reads
     pin-lakefile-require-missing  lakefile.toml has no [[require]] named P
     pin-lakefile-source           P's require is not `git = "<url>"` (a path, Reservoir or
                                   `source` dependency), or its git URL or subDir differs from
                                   the manifest's entry for P, or the manifest entry is not git
     pin-lakefile-rev              its `rev` is not upstream.<P>.rev
     pin-early-main      (warning) its `rev` is a 40-hex commit, not a tag (D26's exception,
                                   allowed only by the owner)
 (b) pin-manifest-package-missing  lake-manifest.json has no package P
     pin-manifest-inputrev         its inputRev is not the lakefile's rev
     pin-manifest-rev              its rev is not upstream.<P>.commit
 (c) pin-toolchain                 lean-toolchain differs from P's lean-toolchain at that commit
     pin-extra-toolchain           a pin_policy.extra_toolchains file differs from lean-toolchain
 (d) pin-shared-package            a package in both manifests (e.g. mathlib) with another rev
     pin-upstream-dir-mismatch     --upstream-dir is a git checkout at another commit
     pin-upstream-dir-unverified   (warning) --upstream-dir is not a git checkout, or its
                                   lean-toolchain / lake-manifest.json differ from its HEAD
     pin-upstream-skipped (info)   neither --upstream-dir nor --fetch: (c) upstream part and (d)
                                   are skipped
 (e) pin-manifest-without-lakefile the manifest or the toolchain changed while lakefile.toml did
                                   not (error; info when the approve-to-pass rule passes)
     pin-source-changed            (with --base) P's manifest url differs from the merge base's
                                   (error; info when approved)
 (f) pin-bump                      --head-config (the PR's own config) pins P at another rev or
                                   commit than the config: the checks above then use the head
                                   pin, and the bump itself needs the owner (error; info when
                                   approved). Only upstream.<P> is read from the head config;
                                   every other setting comes from --config.
     pin-head-config-unreadable    --head-config does not parse or has no valid upstream.<P>

--fetch reads https://raw.githubusercontent.com/<owner>/<repo>/<commit>/{lean-toolchain,
lake-manifest.json}. owner/repo come from P's url in the manifest at the merge base when --base
is given and that manifest has P (the PR's own url only when it equals that one, or the change is
approved), else from P's url in our manifest; owner and repo must match [A-Za-z0-9_.-]+.
Report "pin-policy".
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rk_common as rk  # noqa: E402

HEX40 = re.compile(r"[0-9a-f]{40}")
LAKE_CONFIG_NAMES = ("lakefile.lean", "lakefile.toml")
GITHUB_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")


def manifest_packages(m, where: str) -> dict[str, dict]:
    if not isinstance(m, dict) or not isinstance(m.get("packages"), list):
        raise rk.SetupError(f"{where}: not a Lake manifest (no packages array)")
    out = {}
    for p in m["packages"]:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            out[p["name"]] = p
    return out


def fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode("utf-8")
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as e:
        raise rk.SetupError(f"cannot fetch {url}: {e}") from None


def github_repo(url: str) -> str:
    m = GITHUB_URL.fullmatch(url or "")
    if not m or m.group(1) in (".", "..") or m.group(2) in (".", ".."):
        raise rk.SetupError(f"--fetch: package url {url!r} is not a GitHub repository URL")
    return f"{m.group(1)}/{m.group(2)}"


def norm_url(u) -> str | None:
    if not isinstance(u, str):
        return None
    u = u.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u


def norm_subdir(v):
    return None if v in (None, "", ".") else v


def pin_of(entry: dict) -> dict:
    return {"rev": entry["rev"], "commit": entry["commit"]}


def main(argv):
    p = rk.ArgParser(prog="rk_pinpolicy.py")
    p.add_argument("--config", required=True)
    p.add_argument("--root", default=".")
    up = p.add_mutually_exclusive_group()
    up.add_argument("--upstream-dir")
    up.add_argument("--fetch", action="store_true")
    rk.add_changed_args(p, required=False)
    p.add_argument("--head-config", help="the PR's own config: its upstream.<P> may bump the pin")
    rk.add_approval_args(p)
    a = p.parse_args(argv)
    cfg = rk.load_config(a.config)
    pkg = cfg.need("pin_policy", "package")
    pin = pin_of(cfg.upstream(pkg))
    lakefile_rel = cfg.get("pin_policy", "lakefile")
    manifest_rel = cfg.get("pin_policy", "manifest")
    toolchain_rel = cfg.get("pin_policy", "toolchain")
    if os.path.basename(lakefile_rel) != "lakefile.toml":
        raise rk.SetupError(f"{a.config}: pin_policy.lakefile must name a lakefile.toml "
                            f"(found {lakefile_rel!r})")
    root = a.root
    rep = rk.Report("pin-policy")
    rep.data["package"] = pkg
    owners = cfg.get("owned", "owners")
    approval_cache: list[dict] = []

    def approval() -> dict:
        if not approval_cache:
            approval_cache.append(rk.approval_passes(owners, a.author, a.reviews, a.head_sha))
            rep.data["approval"] = approval_cache[0]
        return approval_cache[0]

    def gated(code: str, msg: str, file: str | None = None) -> None:
        ap = approval()
        if ap["passed"]:
            rep.add(code, f"{msg}; approved: {ap['reason']}", "info", file=file)
        else:
            rep.add(code, f"{msg}; needs the owner's approval on the head commit "
                    f"({ap['reason']})", file=file)

    # (f) a pin bump in the PR's own config
    if a.head_config:
        head_pin = None
        try:
            head_pin = pin_of(rk.load_config(a.head_config).upstream(pkg))
        except rk.SetupError as e:
            rep.add("pin-head-config-unreadable", f"--head-config: {e}", file=a.head_config)
        if head_pin is not None and head_pin != pin:
            gated("pin-bump", f"the head config moves the {pkg} pin from {pin['rev']} "
                  f"({pin['commit'][:12]}) to {head_pin['rev']} ({head_pin['commit'][:12]})",
                  a.head_config)
            rep.data["base_pin"] = pin
            pin = head_pin
    rep.data["pin"] = pin

    # (a) lakefile
    lake_dir = os.path.dirname(lakefile_rel)
    for name in LAKE_CONFIG_NAMES:
        rel = os.path.join(lake_dir, name)
        if os.path.normpath(rel) != os.path.normpath(lakefile_rel) \
                and os.path.lexists(rk.under(root, rel)):
            rep.add("pin-lakefile-shadowed", f"{rel} sits next to {lakefile_rel}: Lake reads "
                    f"{LAKE_CONFIG_NAMES[0]} first, so {lakefile_rel} would not be the "
                    "configuration Lake uses", file=rel)
    lake = rk.load_toml(rk.under(root, lakefile_rel), "lakefile")
    requires = lake.get("require", [])
    if not isinstance(requires, list):
        requires = []
    reqs = [r for r in requires if isinstance(r, dict) and r.get("name") == pkg]
    lake_rev = None
    req = None
    if not reqs:
        rep.add("pin-lakefile-require-missing", f"{lakefile_rel} has no [[require]] named {pkg}",
                file=lakefile_rel)
    else:
        req = reqs[0]
        if len(reqs) > 1:
            rep.add("pin-lakefile-source", f"{lakefile_rel} requires {pkg} {len(reqs)} times",
                    file=lakefile_rel)
        lake_rev = req.get("rev")
        if lake_rev != pin["rev"]:
            rep.add("pin-lakefile-rev", f"{lakefile_rel}: {pkg} rev is {lake_rev!r}, the config "
                    f"pins {pin['rev']!r}", file=lakefile_rel)
        if isinstance(lake_rev, str) and HEX40.fullmatch(lake_rev):
            rep.add("pin-early-main", f"{lakefile_rel}: {pkg} is pinned to the commit {lake_rev}, "
                    "not a tag (D26's early-main exception: the owner's decision)", "warning",
                    file=lakefile_rel)

    # (b) manifest
    ours = manifest_packages(rk.load_json(rk.under(root, manifest_rel), "manifest"), manifest_rel)
    mp = ours.get(pkg)
    if mp is None:
        rep.add("pin-manifest-package-missing", f"{manifest_rel} has no package {pkg}",
                file=manifest_rel)
    else:
        if lake_rev is not None and mp.get("inputRev") != lake_rev:
            rep.add("pin-manifest-inputrev", f"{manifest_rel}: {pkg} inputRev is "
                    f"{mp.get('inputRev')!r}, the lakefile says {lake_rev!r}", file=manifest_rel)
        if mp.get("rev") != pin["commit"]:
            rep.add("pin-manifest-rev", f"{manifest_rel}: {pkg} rev is {mp.get('rev')!r}, the "
                    f"config pins commit {pin['commit']!r}", file=manifest_rel)

    # (a) the require's source agrees with the manifest's entry
    if req is not None:
        others = sorted(k for k in ("path", "source", "scope") if k in req)
        if not isinstance(req.get("git"), str) or others:
            rep.add("pin-lakefile-source", f"{lakefile_rel}: {pkg} must be required as "
                    f"`git = \"<url>\"` with a `rev`; found the key(s) "
                    f"{', '.join(sorted(set(req) - {'name'}))}", file=lakefile_rel)
        elif mp is not None:
            if mp.get("type") != "git":
                rep.add("pin-lakefile-source", f"{manifest_rel}: {pkg} is a {mp.get('type')!r} "
                        "package, not git", file=manifest_rel)
            if norm_url(req["git"]) != norm_url(mp.get("url")):
                rep.add("pin-lakefile-source", f"{lakefile_rel}: {pkg} comes from {req['git']!r},"
                        f" {manifest_rel} from {mp.get('url')!r}", file=lakefile_rel)
            if norm_subdir(req.get("subDir")) != norm_subdir(mp.get("subDir")):
                rep.add("pin-lakefile-source", f"{lakefile_rel}: {pkg} subDir "
                        f"{req.get('subDir')!r}, {manifest_rel} subDir {mp.get('subDir')!r}",
                        file=lakefile_rel)

    # (c) toolchains
    toolchain = rk.read_text(rk.under(root, toolchain_rel), "toolchain").strip()
    rep.data["toolchain"] = toolchain
    for extra in cfg.get("pin_policy", "extra_toolchains"):
        t = rk.read_text(rk.under(root, extra), "toolchain").strip()
        if t != toolchain:
            rep.add("pin-extra-toolchain", f"{extra} is {t!r}, {toolchain_rel} is {toolchain!r}",
                    file=extra)

    # (e) diff mode, part 1: the merge base's manifest url for P (also the --fetch source)
    changed = rk.changed_from_args(a, root)
    fetch_url = mp.get("url", "") if mp is not None else None
    if a.base and mp is not None:
        mb = rk.git(root, "merge-base", a.base, a.head).strip()
        spec = f"{mb}:{manifest_rel.lstrip('/')}"
        try:
            base_m = json.loads(rk.git(root, "show", spec))
            base_mp = manifest_packages(base_m, spec).get(pkg)
        except (rk.SetupError, json.JSONDecodeError):
            base_mp = None  # no manifest at the merge base: nothing to compare with
        if base_mp is not None:
            if norm_url(base_mp.get("url")) != norm_url(mp.get("url")):
                gated("pin-source-changed", f"{manifest_rel}: {pkg}'s url changed from "
                      f"{base_mp.get('url')!r} to {mp.get('url')!r}", manifest_rel)
                if not approval()["passed"]:
                    fetch_url = base_mp.get("url", "")
            rep.data["base_url"] = base_mp.get("url")

    up_toolchain = up_manifest = None
    if a.upstream_dir:
        d = a.upstream_dir
        if not os.path.isdir(d):
            raise rk.SetupError(f"--upstream-dir {d} is not a directory")
        if os.path.exists(os.path.join(d, ".git")):
            head = rk.git(d, "rev-parse", "HEAD").strip()
            rep.data["upstream_head"] = head
            if head != pin["commit"]:
                rep.add("pin-upstream-dir-mismatch", f"--upstream-dir {d} is at {head}, the pin "
                        f"is {pin['commit']}", file=d)
            else:
                dirty = rk.git(d, "status", "--porcelain", "--", "lean-toolchain",
                               "lake-manifest.json").strip()
                if dirty:
                    rep.add("pin-upstream-dir-unverified", f"--upstream-dir {d}: lean-toolchain "
                            "or lake-manifest.json differ from its HEAD", "warning", file=d)
        else:
            rep.add("pin-upstream-dir-unverified", f"--upstream-dir {d} is not a git checkout; "
                    "its commit cannot be verified", "warning", file=d)
        up_toolchain = rk.read_text(os.path.join(d, "lean-toolchain"), "upstream toolchain")
        up_manifest = rk.load_json(os.path.join(d, "lake-manifest.json"), "upstream manifest")
        rep.data["upstream_source"] = d
    elif a.fetch:
        if fetch_url is None:
            raise rk.SetupError(f"--fetch needs {pkg}'s url from {manifest_rel}")
        repo = github_repo(fetch_url)
        base = f"https://raw.githubusercontent.com/{repo}/{pin['commit']}"
        up_toolchain = fetch(base + "/lean-toolchain")
        try:
            up_manifest = json.loads(fetch(base + "/lake-manifest.json"))
        except json.JSONDecodeError as e:
            raise rk.SetupError(f"{base}/lake-manifest.json is not JSON: {e}") from None
        rep.data["upstream_source"] = base
    else:
        rep.add("pin-upstream-skipped", "no --upstream-dir or --fetch: the upstream toolchain "
                "and shared-package checks are skipped", "info")

    if up_toolchain is not None:
        if up_toolchain.strip() != toolchain:
            rep.add("pin-toolchain", f"{toolchain_rel} is {toolchain!r}, {pkg} at "
                    f"{pin['commit'][:12]} uses {up_toolchain.strip()!r}", file=toolchain_rel)
        theirs = manifest_packages(up_manifest, f"{pkg}'s manifest")
        shared = sorted(set(ours) & set(theirs) - {pkg})
        rep.data["shared_packages"] = shared
        for name in shared:
            if ours[name].get("rev") != theirs[name].get("rev"):
                rep.add("pin-shared-package", f"{manifest_rel}: {name} is at "
                        f"{ours[name].get('rev')}, {pkg} at {pin['commit'][:12]} uses "
                        f"{theirs[name].get('rev')}", file=manifest_rel)

    # (e) diff mode, part 2: a manifest or toolchain change goes through the lakefile
    if changed is not None:
        norm = {c.strip("/") for c in changed}
        lake_changed = lakefile_rel.strip("/") in norm
        for rel in (manifest_rel, toolchain_rel):
            if rel.strip("/") in norm and not lake_changed:
                gated("pin-manifest-without-lakefile", f"{rel} changed while {lakefile_rel} did "
                      "not (a bump goes through the lakefile)", rel)
        rep.data["changed"] = len(changed)
    return a.config, [rep]


if __name__ == "__main__":
    rk.run_main("pin-policy", main)
