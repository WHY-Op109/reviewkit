# ReviewKit (working name, Q19)

The reusable review, key-definition and blueprint checks of GLCinLean (D35), as the Lake package
`ReviewKit`. It contains no project-specific code: every module prefix, rule and file path comes
from the project's configuration file. It becomes its own repository once its data format
settles. Status: stage A1 (2026-11-05), unreviewed.

    lake exe review --config <project.toml> [--import Module]... <command> [args]

| Command | What it does |
|---|---|
| `closure` | walks types, definition bodies and constructors from the abstract; enters every configured closure class (for example a reviewed upstream library), stops only at background modules; its output is the key-definition list with tags; fails on project code outside the classes and on unclassified modules |
| `axioms` | project axioms and non-standard axioms fail; every `sorry` reachable from the project is traced to its source (upstream included) and must be in the sorry budget, bound to the statement fingerprint; per-declaration traces, statement level and with proofs; placeholder, proof and copied-proof debt |
| `placeholders` | census of `@[placeholder]`, `opaque` and sorry-valued definitions; untagged placeholders fail |
| `imports` | the import rule by module prefix; warnings for library names the abstract uses directly |
| `pin` | the two-level statement pin: own statement (hard) and transitive digest (stale only) |
| `fingerprint` | statement fingerprint and transitive digest (SHA-256 over a canonical serialisation that keeps declaration identity) |
| `impact` | reverse dependencies, proofs included |
| `faults` | runs fault fixtures, each in its own process, against their expected diagnostics |

All output is JSON (`reviewkit-report/0`). Exit status: 0 no errors, 1 errors, 2 setup failure.
Data formats: `reviewkit-config/0`, `reviewkit-sorry-budget/0`, `reviewkit-statement-pin/0`,
`reviewkit-faults/0` (examples in `test/`).

Own tests (core Lean only):

    lake build RKToy RKToyBad && lake exe review --config test/project.toml faults test/faults.toml

Because it reads `.olean` files and runs project initialisers, it must be built by the project's
own toolchain and belongs inside the build sandbox.
