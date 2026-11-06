# ReviewKit data format v0

Every file and record carries a `format` field `reviewkit-<name>/<version>`. Readers reject a
format or version they do not know and reject unknown keys, so a misspelt field is an error, not
a silent default. Adding an optional field keeps the version; any other change bumps it.

Machine evidence (`evidence/`, written by `review record`) is kept apart from the human-owned
sorry budget and statement pin (`reviews/`). Evidence never says that a person checked anything.

| Format | Typical path | Owner | Read or written by |
|---|---|---|---|
| `reviewkit-config/0` | `project.toml` | human | `review`, `scripts/rk_extras.py` |
| `reviewkit-extras/0` | `extras.file` | project | `rk_extras.py` |
| `reviewkit-extras-join/0` | `--out` | output | `rk_extras.py` |
| `reviewkit-sorry-budget/0` | `reviews/sorry-budget.toml` | human | `review axioms`, `review evidence` |
| `reviewkit-statement-pin/0` | `reviews/statement-pin.toml` | human | `review pin`, `review evidence` |
| `reviewkit-evidence/0` | `evidence/fingerprint/<decl>/<s12>.<d12>.json` | machine | `review record`, `review evidence` |
| `reviewkit-faults/0` | `test/faults.toml` | project | `review faults` |
| `reviewkit-policy-faults/0` | `test/extras/faults.toml` | project | `rk_faults.py` |
| `reviewkit-report/0` | stdout | output | every command |

## `reviewkit-config/0` (TOML)

`test/project.toml` is a complete example. Tables: `project` (`import`, `modules`, `abstract`),
`closure` (`background`, `[[closure.class]]` with `tag` and `modules`), `axioms` (`allowed`,
`budget`), `imports` (`[[imports.rule]]` with `module` and `allow`; the first matching prefix
applies), `foundations_names` (`foundations`, `checked`, `basic`, `required`), `fingerprint`
(`content`, `unfold_abbrevs`, `notations`), `pin` (`file`, `decls`), `placeholders`
(`modules`), `evidence` (`dir`). Two further top-level tables are accepted without validation by
`review`: `papers` (free-form project data) and `extras`, read by `rk_extras.py`:

```toml
[extras]
file = "extras.toml"
providers = ["lib", "local", "placeholder"]
require_provider = true        # every entry names one of `providers`
```

Any other top-level table is a setup error.

## `reviewkit-extras/0` (TOML)

```toml
format = "reviewkit-extras/0"

[decl."Proj.Abstract.Obj"]
provider = "placeholder"       # required when extras.require_provider; one of extras.providers
key = true                     # optional: a key definition
chain = true                   # optional
upstream = "Lib.Obj"           # optional: the provider declaration re-exported
blueprint = "node-id"          # optional
hypotheses = ["…"]             # optional
note = "…"                     # optional
```

Every id must be a node of `tracker graph --all`. Codes (report `extras`): `extras-format`,
`extras-unknown-key`, `extras-type`, `extras-stale-id`, `extras-provider-missing`,
`extras-provider-unknown`; warning `extras-key-outside-closure` (a `key` entry not in the
`review closure` key list, when `--closure` is given).

## `reviewkit-extras-join/0` (JSON)

`{"format", "cards": [...]}`: one card per planned node and per node with an extras entry, with
the graph's fields (`id`, `module`, `kind`, `state`, `planned`, `desc`, `source`, `deps`, and
`wrong`/`deprecated` when set) and the entry's fields.

## `reviewkit-sorry-budget/0` and `reviewkit-statement-pin/0` (TOML, human-owned)

```toml
format = "reviewkit-sorry-budget/0"
[[entry]]
decl = "Proj.Abstract.main"
statement = "sha256:…"          # required: the approved statement fingerprint
digest = "sha256:…"             # optional: the transitive digest at approval (stale only)
evidence = "fingerprint:Proj.Abstract.main@…"   # optional: the fingerprint record
source = "…"                    # optional, descriptive
kind = "…"                      # optional, descriptive
approved = ""                   # owner: "YYYY-MM-DD <initials>"; never read by the kit
```

The pin file has the same fields under `[[pin]]`, without `source` and `kind`. A changed
`statement` is an error, a changed `digest` a warning. When `evidence` is set, the record must
exist and hold the same declaration and fingerprints: `budget-evidence-missing`,
`budget-evidence-mismatch`, `pin-evidence-missing`, `pin-evidence-mismatch` (errors).

## `reviewkit-evidence/0` (JSON, machine)

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
  "producer": {"tool": "reviewkit", "kit_version": "0.3.0", "lean_version": "4.36.0-rc2",
               "config": "project.toml", "config_sha256": "sha256:…"}
}
```

`review evidence` codes: `evidence-format`, `evidence-duplicate-id`, `evidence-id-mismatch` (id
does not match the fingerprints or the path), `evidence-hash-mismatch` (a text does not hash to
its fingerprint), all errors; info `evidence-outdated` (the declaration's fingerprints changed).

## `reviewkit-faults/0` (TOML, `review faults`)

```toml
format = "reviewkit-faults/0"
[[case]]
name = "pin: statement changed"
kind = "fault"                 # fault | control | fpcompare
modules = ["Proj.Bad.Swap"]    # extra modules to import
command = "pin"                # any check command; default all
args = []
abstract = "…"                 # optional overrides: abstract, budget, pin, evidence, pin_decls
orders = ["forward", "reverse"]   # root orders to run; default forward
[[case.stand_in]]              # check `decl` as if it were `for`
decl = "Proj.Bad.Swap.main"
for = "Proj.Abstract.main"
[[case.expect]]
code = "pin-statement-mismatch"
decl = "Proj.Abstract.main"    # optional
severity = "error"             # default error
```

Also `[[case.class]]` (extra closure classes), `[[case.trace]]` (`decl`, `contains`),
`[[case.impact]]` (`decl`, `includes`), `[[case.key]]` (`decl`, `tag`) and, for `fpcompare`,
`[[case.compare]]` (`a`, `b`, `level` = statement | digest, `same`). A fault passes when every
expect is produced and every error is expected; a control passes when it produces no unexpected
error; a setup failure always fails the case. Each case runs in its own process.

## `reviewkit-policy-faults/0` (TOML, `rk_faults.py`)

```toml
format = "reviewkit-policy-faults/0"
[[case]]
name = "extras: an id that is no graph node"
kind = "fault"                 # fault | control
command = "extras"             # the only command
dir = "."                      # optional, relative to this file; the command runs there
args = ["--config", "project.toml", "--graph", "graph.json", "--extras", "extras-stale.toml"]
stdin = ""                     # optional
[[case.expect]]
code = "extras-stale-id"
decl = "Toy.A.renamed"         # optional; also `file`
severity = "error"             # default error
```

Same semantics as `reviewkit-faults/0`; in addition, the exit status must agree with the errors
reported, and the output must be a `reviewkit-report/0` envelope with `reports`.

## `reviewkit-report/0` (JSON, stdout)

An envelope `{format, command, config, ok}` with one body: `reports` (an array of
`{check, ok, errors, warnings, diagnostics, …data}`), `faults`, `fingerprints`, `records`,
`sha256_vectors`, or `setup_error`. A report's `ok` is true exactly when it has no error; the
envelope's `ok` when every report's is. The `--toml` modes of `axioms` and `pin` print TOML
instead.

## Conventions

* **Declaration ids** are absolute Lean names as `Name.toString` prints them.
* **Fingerprints** are `sha256:` and 64 lowercase hex digits, over the canonical serialisation
  `reviewkit-canonical/0` that `review fingerprint --text` prints (de Bruijn indices, no binder
  names, binder kinds kept, no metadata, universe parameters by position, configured notations
  unfolded; see `ReviewKit/Fingerprint.lean`).
  * **Statement fingerprint**: `sha256(statement text)`; other constants by qualified name only.
  * **Transitive digest**: over the digest text, where a content constant appears as
    `name#<its digest>` and a background constant as `name:<16 hex of its type hash>`. With
    `H(s) = "sha256:" + hex(SHA-256(UTF-8 s))`: a declaration alone in its block has digest
    `H(text)`; member `i` of an inductive block of size > 1 has `H("M " + i + " " + H(text))`.
  * Opaque constants are serialised by kind and type only. A `sorry` in an opaque value is seen
    by `review axioms`, not by fingerprints.
* **Evidence ids**: `fingerprint:<decl>@<s12>.<d12>` (first 12 hex digits of the statement
  fingerprint and the digest), stored at `<evidence dir>/fingerprint/<decl path>/<s12>.<d12>.json`;
  `<decl path>` is the id with every character outside `[A-Za-z0-9._-]` replaced by `_xHHHH_`.
  Records have no timestamps: re-recording an unchanged declaration rewrites an identical file.
* **Diagnostics**: `{check, code, severity: error|warning|info, message, decl?, module?, path?,
  file?}`; `code` is stable and fixtures match on it.
* **Exit status** of every command: 0 no errors, 1 errors (or a fixture not matched), 2 setup
  failure (missing input, bad arguments, bad configuration, a crash).
