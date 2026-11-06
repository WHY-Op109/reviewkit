# ReviewKit (working name, Q19)

The reusable review, key-definition and blueprint checks of GLCinLean (D35), as the Lake package
`ReviewKit` plus a few standard-library Python checks. It contains no project-specific code:
every module prefix, rule, owner and file path comes from the project's configuration file. It
becomes its own repository once its data format settles. Status: stage A2 (2026-11-06),
unreviewed. Data format v0 is in [`FORMATS.md`](FORMATS.md); the JSON Schemas are in `schemas/`.

## `review` (Lean, K1)

    lake exe review --config <project.toml> [--import Module]... [--evidence-dir D] [--attestations F] <command> [args]

| Command | What it does |
|---|---|
| `all` | `closure`, `axioms`, `placeholders`, `imports`, `pin`, `evidence`, `attestations` |
| `closure` | walks types, definition bodies and constructors from the abstract; enters every configured closure class (for example a reviewed upstream library), stops only at background modules; its output is the key-definition list with tags; fails on project code outside the classes and on unclassified modules |
| `axioms` | project axioms and non-standard axioms fail; every `sorry` reachable from the project is traced to its source (upstream included), **opaque bodies included** (an `opaque x : T := sorry` is a source), and must be in the sorry budget, bound to the statement fingerprint; per-declaration traces, statement level and with proofs; placeholder, proof and copied-proof debt. `--toml` prints budget entries |
| `placeholders` | census of `@[placeholder]`, `opaque` and sorry-valued definitions; untagged placeholders fail; a sorry witness is a warning |
| `imports` | the import rule by module prefix; warnings for library names the abstract uses directly |
| `pin` | the two-level statement pin: own statement (hard) and transitive digest (stale only). `--toml` prints pin entries |
| `evidence` | the evidence records (each parses, its id matches its fingerprints and path, its texts hash to its fingerprints) and the budget's and pin's links to evidence and attestations |
| `attestations` | computes for every human attestation whether each binding is current, stale or broken; output only, never written into the attestation file (D43/F11) |
| `record [--closure] [D...]` | the D9 record command: writes a fingerprint evidence record (SHA-256 and canonical text, statement and digest) per declaration; default: the pinned and budgeted declarations |
| `attest --kind K --by B --initials I --date YYYY-MM-DD --scope S [--id ID] D...` | writes the evidence for `D...` and prints an attestation block bound to it, for the owner to append |
| `fingerprint D... [--text]` | statement fingerprint and transitive digest |
| `impact D...` | reverse dependencies, proofs included |
| `faults <faults.toml>` | runs fault fixtures, each in its own process, against their expected diagnostics |
| `selftest` | SHA-256 test vectors |

All output is JSON (`reviewkit-report/0`) except the `--toml` modes and `attest`. Exit status:
0 no errors, 1 errors, 2 setup failure. Because it reads `.olean` files and runs project
initialisers, it must be built by the project's own toolchain and belongs inside the build
sandbox.

Opaque values: the axiom audit follows them (they can hide an admission); the closure walk and
both fingerprints do not (the kernel never unfolds an opaque value, so its witness cannot change
what a statement means).

## Python checks (`scripts/`, K4 and data validation)

Standard library only (Python 3.11+), run as `python3 -I`. Each prints `reviewkit-report/0`.

| Script | What it does |
|---|---|
| `rk_extras.py` | joins `tracker graph --all` with the extras file; fails on stale ids, unknown keys, missing or unknown providers |
| `rk_owned.py` | owned paths changed (fails unless an owner approved the head commit or authored the PR); CODEOWNERS covers every owned path |
| `rk_flagged.py` | D19 flagged entries: edits to the plan entries of flagged declarations, and changes of the flags themselves, go to the owner |
| `rk_pinpolicy.py` | lakefile, manifest and toolchain agree with the pinned upstream tag; a manifest change without a lakefile change is flagged |
| `rk_snapshot.py` | tracker state snapshots and their comparison (the no-progress guard) |
| `rk_schema.py` | validates data files and records against `schemas/` by their `format` |
| `rk_faults.py` | runs `reviewkit-policy-faults/0` fixtures |
| `mint-app-token.py` | mints a GitHub App installation token (the loop's identity, Stage C) |

## Own tests

    lake build RKToy RKToyBad && lake exe review --config test/project.toml faults test/faults.toml
    python3 -I scripts/rk_faults.py test/policy/faults.toml
    python3 -I scripts/rk_faults.py test/schemas/faults.toml
