# ReviewKit

Checks that protect a formal statement in a Lean project: the closure of the statement's
definitions, a scoped axiom audit with a fingerprint-bound sorry budget, a placeholder census,
import rules, a two-level statement pin, and fingerprint evidence records. It is the Lake package
`ReviewKit` (core Lean only) plus two standard-library Python scripts. It names no project:
every module prefix, rule and path comes from the project's configuration file. Data formats are
in [`FORMATS.md`](FORMATS.md).

## `review`

    lake exe review --config <project.toml> [--import Module]... [--evidence-dir D] <command> [args]

| Command | What it does |
|---|---|
| `all` | `closure`, `axioms`, `placeholders`, `imports`, `pin`, `evidence` |
| `closure` | walks types, definition bodies and constructors from the abstract through the configured closure classes, stopping at background modules; prints the key-definition list; fails on project code outside the classes and on unclassified modules |
| `axioms [--toml]` | project and non-standard axioms fail; every reachable `sorry` (opaque bodies included) is traced to its source and must be in the sorry budget, bound to its statement fingerprint. `--toml` prints budget entries |
| `placeholders` | census of `@[placeholder]`, `opaque` and sorry-valued definitions; untagged placeholders fail |
| `imports` | import rules by module prefix; warnings for library names the abstract uses directly |
| `pin [--toml]` | statement pin: own statement (hard) and transitive digest (stale, warning). `--toml` prints pin entries |
| `evidence` | checks the evidence records and the budget's and pin's links to them |
| `record [--closure] [D...]` | writes a fingerprint evidence record per declaration; default: the pinned and budgeted declarations; `--closure` adds the key-definition list |
| `fingerprint D... [--text]` | statement fingerprint and transitive digest |
| `impact D...` | reverse dependencies, proofs included |
| `faults <faults.toml>` | runs fault fixtures, each in its own process |
| `selftest` | SHA-256 test vectors |

Output is JSON (`reviewkit-report/0`) except the `--toml` modes. Exit status: 0 no errors,
1 errors, 2 setup failure.

## Python (`scripts/`, Python 3.11+, run as `python3 -I`)

| Script | What it does |
|---|---|
| `rk_extras.py` | joins `tracker graph --all` with the project's extras file; fails on stale ids, unknown keys, missing or unknown providers |
| `rk_faults.py` | runs `reviewkit-policy-faults/0` fixtures against `rk_extras.py` |

## Tests

    lake build RKToy RKToyBad
    lake exe review --config test/project.toml faults test/faults.toml
    python3 -I scripts/rk_faults.py test/extras/faults.toml

## Using it in a project

```toml
[[require]]
name = "ReviewKit"
git = "https://github.com/WHY-Op109/reviewkit"
rev = "<commit>"
```

`review` reads the project's `.olean` files and runs its initialisers, so it must be built by the
project's own toolchain (`lake exe review` in the project) and run inside the build sandbox.
