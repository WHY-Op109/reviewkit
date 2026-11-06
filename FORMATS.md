# ReviewKit data format v0

Status: written in stage A2 (2026-11-06) by Claude Opus 5.5; unreviewed. Version 0 fixes the
formats that stage A1 introduced under the same identifiers (`reviewkit-config/0`,
`reviewkit-sorry-budget/0`, `reviewkit-statement-pin/0`, `reviewkit-faults/0`,
`reviewkit-report/0`) and adds the rest. Every A1 file stays valid. The identifiers name no
project; a rename of the kit (Q19) is a folder move.

Every file and record carries a `format` field `reviewkit-<name>/<version>`. A reader rejects a
format or version it does not know, and rejects unknown keys (so a misspelt field is an error,
not a silent default). The JSON Schema of each format is `schemas/reviewkit-<name>-<version>.schema.json`
(JSON Schema 2020-12, the subset `scripts/rk_schema.py` implements); TOML files are validated as
the JSON value TOML decodes to. A change that adds an optional field keeps the version; any
other change bumps it, and the kit then reads both versions until the old one is retired.

Two classes of record are kept apart (D43/F11):

* **machine evidence** (`evidence/`, machine-writable): fingerprint records written by
  `review record`; later (A3) read-back and comparator records. Evidence never says that a
  person checked anything.
* **human attestations** (`reviews/`, human-owned): the sorry budget, the statement pin and the
  attestation records. An attestation is bound to fingerprints and evidence ids; whether it is
  current or stale is **computed** from those bindings by `review attestations`, never written
  into `reviews/`.

## Files the kit reads (data)

| Format | Typical path | Owner | Read by |
|---|---|---|---|
| `reviewkit-config/0` | `<project>/project.toml` | human (policy, D43/F04) | `review`, `scripts/rk_*.py` |
| `reviewkit-extras/0` | `extras.file` | agents (flagged fields go to the owner, D19) | `rk_extras.py`, `rk_flagged.py` |
| `reviewkit-sorry-budget/0` | `reviews/sorry-budget.toml` | human | `review axioms`, `review evidence` |
| `reviewkit-statement-pin/0` | `reviews/statement-pin.toml` | human | `review pin`, `review evidence` |
| `reviewkit-attestations/0` | `reviews/attestations.toml` | human | `review attestations` |
| `reviewkit-evidence/0` | `evidence/fingerprint/<decl>/<s12>.<d12>.json` | machine | `review evidence`, `review attestations` |
| `reviewkit-faults/0` | the project's fault file, `test/faults.toml` | project | `review faults` |
| `reviewkit-policy-faults/0` | the project's policy fault file, `test/policy/faults.toml`, `test/schemas/faults.toml` | project | `rk_faults.py` |

## Records agents and tools write (outputs)

| Format | Written by | Notes |
|---|---|---|
| `reviewkit-report/0` | `review`, `rk_*.py` | every check's output |
| `reviewkit-extras-join/0` | `rk_extras.py` | cards: tracker graph joined with extras (K11 input) |
| `reviewkit-snapshot/0` | `rk_snapshot.py` | per-node tracker states for the no-progress guard (B6.5) |
| `reviewkit-task/0` | the driver (K9, B2) | one task |
| `reviewkit-worker-result/0` | a worker (B2) | outcome, reason, suspicions, plan edits, wrong marks |
| `reviewkit-readback/0` | the read-back harness (K5, A3) | blind read-back, with the model's output under `output` |
| `reviewkit-comparator-verdict/0` | the comparator (K6, A3) | machine verdict; owner and expert fields kept apart (D43/F06) |
| `reviewkit-blueprint-revision/0` | an Astra repair call (D21) | plan and blueprint edits |
| `reviewkit-scout-digest/0` | the scout (K13) | daily digest; hits never reset a review (D39) |

The model-facing part of an agent record is its `output` object: the harness passes
`$defs/output` of the record's schema to the model (`--json-schema` / `--output-schema`) and
wraps the validated result with the fields only the host knows (model, effort, prompt and
schema hashes, usage), so a model can never write its own provenance.

## Shared conventions

* **Declaration ids** are absolute Lean names as printed by `Name.toString`
  (`Proj.Abstract.main`), the same ids `tracker graph` uses (D12).
* **Fingerprints** are `sha256:` followed by 64 lowercase hex digits.
* **Canonical serialisation** `reviewkit-canonical/0`: the text `review fingerprint --text`
  prints (de Bruijn indices, no binder names, binder kinds kept, no metadata, universe
  parameters by position, the configured notations unfolded; `Fingerprint.lean` documents it).
  * The **statement fingerprint** is `sha256(statement text)`, where other constants are
    referenced by qualified name only.
  * The **transitive digest** is computed over the *digest text*, in which a content constant is
    referenced as `name#<its digest>` and a background constant as `name:<16 hex of its type
    hash>`. Write `H(s)` for `"sha256:" + hex(SHA-256(UTF-8 of s))`. For a declaration alone in
    its block, digest = `H(digest text)`. For a member of an inductive block of size > 1 at
    position `i`, digest = `H("M " + i + " " + H(block digest text))`.
  * Opaque constants are serialised by kind and type only, at both levels: the kernel never
    unfolds an opaque value, so its witness cannot change what any statement means. A `sorry`
    inside an opaque value is seen by the axiom audit (`review axioms`), not by fingerprints.
* **Evidence ids** of fingerprint records: `fingerprint:<decl>@<s12>.<d12>`, where `<s12>` and
  `<d12>` are the first 12 hex digits of the statement fingerprint and the digest. The record is
  stored at `<evidence dir>/fingerprint/<decl path>/<s12>.<d12>.json`, where `<decl path>` is the
  declaration id with every character outside `[A-Za-z0-9._-]` replaced by `_xHHHH_` (its code
  point in 4+ hex digits). Records are deterministic: no timestamps, so re-recording an unchanged
  declaration rewrites an identical file.
* **Reports** (`reviewkit-report/0`): an envelope `{format, command, config, ok}` with one body:
  `reports` (an array of check reports `{check, ok, errors, warnings, diagnostics, …data}`),
  `faults`, `fingerprints`, `records` (`review record`), `sha256_vectors`, or `setup_error`.
  A report's `ok` is true exactly when it has no error; the envelope's `ok` is true exactly when
  every report is. Diagnostics: `{check, code, severity: error|warning|info, message, decl?,
  module?, path?, file?}`; `code` is stable and fault fixtures match on it. Exit status of every
  command: 0 no errors, 1 errors, 2 setup failure (a missing input file, bad arguments, a crash).
* **Approval context** (policy checks): the PR author's login and the PR's reviews as GitHub's
  REST API returns them (`GET /repos/{o}/{r}/pulls/{n}/reviews`). An owner approval counts only
  if it is the reviewer's latest non-comment review, its state is `APPROVED`, its `commit_id` is
  the PR's head commit, and the reviewer is in `owned.owners`. Comment and pending reviews are
  skipped; a later changes-requested or dismissed review voids an earlier approval; reviews are
  ordered by `submitted_at` (UTC); a review without a user is ignored; logins compare without
  case. A PR authored by an owner passes the approve-to-pass rules (the owner cannot approve
  their own PR and merges with the bypass); this also passes other commits pushed to that PR,
  which is accepted: only owners and the agent identity can push to the repository.
* **What judges a PR**: the checker (`reviewkit/`) and the config come from the default branch,
  never from the PR (D43/F04), and only a PR into the default branch is judged (any other base
  could carry its own checker). The policy checks compare the PR's merge base (`--base-root`,
  `--base`) with its head.

## `reviewkit-config/0` (project configuration, TOML)

A1's tables (`project`, `closure`, `axioms`, `imports`, `foundations_names`, `fingerprint`,
`pin`, `placeholders`, and the reserved `papers`, `scout`, `upstream`) are unchanged; see
`test/project.toml` for a complete example. Version 0 adds:

```toml
[evidence]
dir = "evidence"                        # machine evidence records (relative to the working dir)

[attestations]
file = "reviews/attestations.toml"      # human attestation records

[extras]
file = "proj/extras.toml"
providers = ["mathlib", "tauceti", "stacks", "staging", "placeholder", "abstract"]
require_provider = true                 # every entry names its provider (K8)

[tracker]
plans = "blueprint/plans"               # passed as --dir
roots = ["Proj"]                        # passed as --roots

[owned]                                 # human-owned paths (D9, D44, AGENTS.md item 2)
owners = ["owner-login"]                # GitHub logins
paths = ["/.github/", "/AGENTS.md", "/lakefile.toml"]   # CODEOWNERS-style: a trailing / is a directory
codeowners = ".github/CODEOWNERS"       # checked to cover every owned path with an owner

[flagged]                               # D19: plan entries whose edits go to the owner
flags = ["key", "chain"]                # an extras entry with any of these set to true flags its node
plan_fields = ["desc", "description", "source", "kind", "deps", "wrong", "deprecated"]

[pin_policy]                            # D44: lakefile, manifest and toolchain agree
package = "TauCeti"                     # the pinned upstream package (its tag is in lakefile.toml)
lakefile = "lakefile.toml"
manifest = "lake-manifest.json"
toolchain = "lean-toolchain"
extra_toolchains = ["reviewkit/lean-toolchain"]   # must equal the project toolchain
```

`upstream.<package>` (A1) holds `rev` (the tag), `commit` (the tag's commit) and `policy`.

## `reviewkit-extras/0` (project extras keyed by declaration id, TOML; D12, K8)

```toml
format = "reviewkit-extras/0"

[decl."Proj.Abstract.Dmod"]
provider = "placeholder"       # required when extras.require_provider; one of extras.providers
key = true                     # optional: a key definition (stands for an object the statement names)
chain = true                   # optional: a member of the 𝕃_G chain (D39)
upstream = "TauCeti.ReductiveAffineGroupSchemeCat"   # optional: the provider declaration re-exported
blueprint = "node-id"          # optional: the Markdown blueprint node (D19)
hypotheses = ["…"]             # optional: hypotheses the card lists
note = "…"                     # optional
```

Every `decl` id must be a node of `tracker graph --all` (a stale id after a rename fails);
review tags and fingerprints never go here (they live in `evidence/` and `reviews/`).

Join diagnostics (`rk_extras.py`): `extras-format`, `extras-unknown-key`, `extras-type`,
`extras-stale-id`, `extras-provider-missing`, `extras-provider-unknown`, and the warning
`extras-key-outside-closure` (a `key` entry that is not in the `review closure` key list, when
the closure report is given).

## `reviewkit-sorry-budget/0` and `reviewkit-statement-pin/0` (TOML, human-owned)

```toml
format = "reviewkit-sorry-budget/0"
[[entry]]
decl = "Proj.Abstract.main"
statement = "sha256:…"          # required: the approved statement fingerprint
digest = "sha256:…"             # optional: the transitive digest at approval (stale-only)
evidence = "fingerprint:Proj.Abstract.main@…"   # optional: the fingerprint record
source = "Paper, Conj. 1.6.7"   # optional, descriptive
kind = "…"                      # optional, descriptive
approved = ""                   # owner: "YYYY-MM-DD <initials>"; the kit never reads it
attestation = ""                # owner: id of the attestation holding the approval's scope wording
```

The pin file has the same fields under `[[pin]]`, without `source` and `kind`. The kit checks
`statement` (hard) and `digest` (stale only) as in A1, and, when `evidence` is set, that the
record exists and holds the same statement fingerprint (`budget-evidence-missing`,
`budget-evidence-mismatch`, `pin-evidence-missing`, `pin-evidence-mismatch`, all errors), and
that a non-empty `attestation` names an existing attestation (`budget-attestation-unknown`,
`pin-attestation-unknown`).

## `reviewkit-evidence/0` (machine evidence, JSON)

A fingerprint record (`kind = "fingerprint"`), written by `review record`:

```json
{
  "format": "reviewkit-evidence/0",
  "kind": "fingerprint",
  "id": "fingerprint:Proj.Abstract.main@d56a67b6a163.42296b555afd",
  "decl": "Proj.Abstract.main",
  "decl_kind": "theorem",
  "module": "Proj.Abstract",
  "serialisation": "reviewkit-canonical/0",
  "statement": {"fingerprint": "sha256:…", "text": "…"},
  "digest": {"fingerprint": "sha256:…", "text": "…", "block_index": 0, "block_size": 1},
  "producer": {"tool": "reviewkit", "kit_version": "0.2.0", "lean_version": "4.36.0-rc2",
               "config": "proj/project.toml", "config_sha256": "sha256:…"}
}
```

`review evidence` checks every record (`evidence-format`, `evidence-id-mismatch` when the id
does not match the fingerprints or the path, `evidence-hash-mismatch` when a text does not hash
to its fingerprint: all errors) and reports, as information, whether each record is current
(its fingerprints equal the declaration's now). Read-back and comparator evidence (A3) use the
`readback` and `comparator` kinds with the record formats below.

## `reviewkit-attestations/0` (human attestations, TOML)

```toml
format = "reviewkit-attestations/0"

[[attestation]]
id = "2026-11-20-main-baseline"          # unique, chosen by the owner; never reused
kind = "provisional-baseline"            # provisional-baseline | owner-check | expert-review
by = "owner"                             # owner | expert
initials = "HW"
date = "2026-11-20"
scope = "Provisional goal-1a approval of this declaration's interface baseline under D43/F10. Definition fidelity is tracked separately."
[[attestation.binding]]
decl = "Proj.Abstract.main"
statement = "sha256:…"                   # required
digest = "sha256:…"                      # optional; if set, a digest change makes it stale
evidence = ["fingerprint:Proj.Abstract.main@…"]   # at least one evidence id
```

`kind` keeps the owner's check (tier 4, `owner-check`) and the expert's review (tier 5,
`expert-review`, recorded separately and gating nothing, D43/F06) apart from a provisional
baseline (D44). `review attest` prints such a block bound to the current fingerprints and writes
the evidence records it names; the owner appends it. `review attestations` computes per binding:
`current`, `stale-digest`, `stale-statement`, `decl-missing` (warnings for the stale and missing
states: re-review is queued) or `evidence-missing`, `evidence-mismatch` (errors: the binding
names no record, or a record with other fingerprints). Diagnostic codes are
`attestation-<state>`, plus `attestation-format` and `attestation-duplicate-id`.

## `reviewkit-policy-faults/0` (fixtures for the Python checks, TOML)

```toml
format = "reviewkit-policy-faults/0"
[[case]]
name = "extras: an id that is no graph node"
kind = "fault"                 # fault | control
command = "extras"             # extras | owned-paths | flagged | pin-policy | snapshot | schema
dir = "extras-stale"           # fixture directory, relative to this file; the command runs there
args = ["--graph", "graph.json", "--extras", "extras.toml"]
[[case.expect]]
code = "extras-stale-id"
decl = "X.y"                   # optional; also `file` (optional)
severity = "error"             # default error
```

Semantics as for `reviewkit-faults/0`: a fault passes only if every expected diagnostic is
produced and every error is expected; a control passes only if it produces no unexpected error;
a crash or setup failure (exit 2) always fails the case.

## Policy checks (`scripts/`): options and diagnostic codes

All take `--config C` and, where they compare a PR with its base, `--root DIR` (the head tree),
`--changed-files FILE | --base REV --head REV`, and the approval context `--author LOGIN
--reviews REVIEWS.json --head-sha SHA`. "Approve-to-pass" codes are errors, or `info` with the
`-approved` suffix when approval passes.

| Script (report) | Codes |
|---|---|
| `rk_extras.py` (`extras`) | `extras-format`, `extras-unknown-key`, `extras-type`, `extras-stale-id`, `extras-provider-missing`, `extras-provider-unknown`; warning `extras-key-outside-closure` |
| `rk_owned.py` (`owned-paths`) | approve-to-pass `owned-path-changed` / `owned-path-approved`; `codeowners-missing`, `codeowners-file-missing`, `codeowners-unsupported`. CODEOWNERS semantics: last matching line wins, gitignore-style patterns, a single `*` in a last segment does not cross `/` |
| `rk_flagged.py` (`flagged-entries`) | approve-to-pass `flagged-node-removed`, `flagged-node-edited`, `flagged-flag-changed`, `flagged-entry-edited` / `flagged-approved`; `flagged-unreadable`; info `flagged-no-base-extras`. Option `--flags-root DIR` (repeatable): the flagged set is the union of the base's and these trees' flags. Plan ids resolve as tracker does: the plan's `namespace` is prefixed unless the id starts with `_root_.` |
| `rk_pinpolicy.py` (`pin-policy`) | `pin-lakefile-require-missing`, `pin-lakefile-rev`, `pin-lakefile-source`, `pin-lakefile-shadowed`, `pin-manifest-package-missing`, `pin-manifest-inputrev`, `pin-manifest-rev`, `pin-toolchain`, `pin-extra-toolchain`, `pin-shared-package`, `pin-upstream-dir-mismatch`, `pin-head-config-unreadable`; warnings `pin-early-main`, `pin-upstream-dir-unverified`; info `pin-upstream-skipped`; approve-to-pass `pin-manifest-without-lakefile`, `pin-bump`, `pin-source-changed`. Options `--upstream-dir DIR` or `--fetch` (the upstream's own pins), `--head-config C2` (only `upstream.<package>` is read from it: a pin bump) |
| `rk_leanexec.py` (`lean-exec`) | approve-to-pass `lean-exec-changed` / `lean-exec-approved`: a changed `.lean` file containing code that runs at build or import time (initialisers, `#eval`, `run_*`, elaborators, macros, simprocs, native or unsafe code, meta attributes), or a `lakefile.lean`; a text heuristic |
| `rk_snapshot.py` (`snapshot`) | warnings `snapshot-no-progress`, `snapshot-regression`; info `snapshot-progress`, `snapshot-wrong-new`, `snapshot-deprecated-new`, `snapshot-added`, `snapshot-removed`. Deprecated or wrong nodes are not ranked |
| `rk_schema.py` (`schema`) | `schema-invalid`, `schema-missing-format`, `schema-unknown-format`, `schema-format-mismatch` (with `check --expect FORMAT`), `schema-parse-error`; lint: `schema-unsupported-keyword`, `schema-ref-unresolved`, `schema-id-mismatch`, `schema-malformed`. `pattern` follows JSON Schema's regex rules (ECMA-262 subset), not Python's |
| `rk_faults.py` (`faults`) | a case fails unless its output is a `reviewkit-report/0` envelope with reports, and its exit status agrees with the errors |

Agent records (`reviewkit-worker-result/0`, `reviewkit-blueprint-revision/0`): a plan edit with
`node: null` names a module-level field (`namespace`, `desc` or `description`).
