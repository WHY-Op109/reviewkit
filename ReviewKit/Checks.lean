/-
ReviewKit: the checks of K1 — closure, axioms (scoped, with the fingerprint-bound sorry budget),
placeholders, imports (with the Foundations-name warning), statement pin and impact.
-/
import Lean
import ReviewKit.Core
import ReviewKit.Fingerprint

open Lean

namespace ReviewKit

/-! ## (a) Closure: the key-definition list (D39, D40 Finding 2) -/

def pathTo (parent : Std.HashMap Name Name) (n : Name) : Array Name := Id.run do
  let mut out := #[n]
  let mut cur := n
  for _ in [0:10000] do
    match parent[cur]? with
    | some p => out := out.push p; cur := p
    | none => break
  return out.reverse

def runClosure (cfg : Config) (env : Environment) : Report := Id.run do
  let roots := constsOfModule env cfg.abstract
  let mut diags : Array Diag := #[]
  if roots.isEmpty then
    diags := diags.push { check := "closure", code := "closure-no-roots", modName := cfg.abstract, message := s!"the abstract module {cfg.abstract} is not loaded or empty" }
  let mut stack : Array Name := roots
  let mut parent : Std.HashMap Name Name := {}
  let mut visited : NameSet := {}
  let mut direct : NameSet := {}
  let mut background := 0
  let mut entries : Array (Name × String × Name) := #[]
  while !stack.isEmpty do
    let n := stack.back!
    stack := stack.pop
    if visited.contains n then continue
    visited := visited.insert n
    let some m := moduleOf? env n | continue
    match classify cfg m with
    | .background => background := background + 1
    | .otherProject =>
      diags := diags.push { check := "closure", code := "closure-outside-allowed", decl := n, modName := m, path := pathTo parent n, message := s!"{n} (module {m}) is project code outside every closure class, reached from the abstract" }
    | .unclassified =>
      diags := diags.push { check := "closure", code := "closure-unclassified-module", decl := n, modName := m, path := pathTo parent n, message := s!"{n}: module {m} is neither background, project nor a closure class" }
    | .tagged tag =>
      if isUserFacing env n then entries := entries.push (n, tag, m)
      let some info := env.find? n | continue
      let fromProject := isProjectModule cfg m
      for d in depsOf info (proofs := false) do
        unless visited.contains d do
          unless parent.contains d do parent := parent.insert d n
          if fromProject then direct := direct.insert d
          stack := stack.push d
  let sorted := entries.qsort (fun a b => a.2.1 < b.2.1 || (a.2.1 == b.2.1 && a.1.toString < b.1.toString))
  let mut counts : Std.HashMap String Nat := {}
  let mut directCounts : Std.HashMap String Nat := {}
  for (n, tag, _) in sorted do
    counts := counts.insert tag (counts.getD tag 0 + 1)
    if direct.contains n then directCounts := directCounts.insert tag (directCounts.getD tag 0 + 1)
  let tags := cfg.classes.map (·.tag)
  let entryJson := sorted.map fun (n, tag, m) =>
    let base := [("decl", nameJson n), ("tag", Json.str tag), ("module", nameJson m),
      ("kind", Json.str ((env.find? n).map kindOf |>.getD "?")),
      ("direct_from_project", Json.bool (direct.contains n))]
    Json.mkObj (base ++ match placeholderKind? env n with
      | some k => [("placeholder", Json.str k)]
      | none => [])
  { check := "closure", diags, data := [("roots", toJson roots.size), ("visited", toJson visited.size), ("background_constants", toJson background), ("counts", Json.mkObj (tags.toList.map fun t => (t, toJson (counts.getD t 0)))), ("counts_direct_from_project", Json.mkObj (tags.toList.map fun t => (t, toJson (directCounts.getD t 0)))), ("key_list", Json.arr entryJson)] }

/-! ## (b) Axioms: scoped audit with the sorry budget (Q13, D43/F21) -/

structure AxState where
  memo : Std.HashMap Name NameSet := {}
  inProgress : NameSet := {}
  cycles : Array Name := #[]

/-- Axioms a declaration depends on. Inductive blocks are one node, so the result is the same
for every traversal order (D43/F21: the prototype's sentinel could memoise an incomplete set for
a constructor). An unexpected cycle outside a block is recorded, never silently memoised. -/
partial def axiomsOf (env : Environment) (proofs : Bool) (n : Name) : StateM AxState NameSet := do
  let members := blockOf env n
  let rep := members[0]!
  if let some s := (← get).memo[rep]? then return s
  if (← get).inProgress.contains rep then
    modify fun s => { s with cycles := s.cycles.push rep }
    return {}
  modify fun s => { s with inProgress := s.inProgress.insert rep }
  let mut acc : NameSet := {}
  for m in members do
    let some info := env.find? m | continue
    if info matches .axiomInfo _ then acc := acc.insert m
    for d in depsOf info proofs do
      unless members.contains d do
        for a in (← axiomsOf env proofs d) do acc := acc.insert a
  modify fun s => { s with inProgress := s.inProgress.erase rep, memo := s.memo.insert rep acc }
  return acc

/-- One budget or pin entry (`reviewkit-sorry-budget/0`, `reviewkit-statement-pin/0`). -/
structure FpEntry where
  decl : Name
  statement : String
  digest : String
  /-- The id of the fingerprint evidence record the entry was generated from (optional). -/
  evidence : String := ""
  /-- The id of the owner attestation that holds the approval's scope wording (optional). -/
  attestation : String := ""
  deriving Inhabited

/-- The keys a budget entry (`entry`) or a pin entry (`pin`) may have. `approved` is the owner's
"YYYY-MM-DD <initials>"; the kit checks only that it is a string and never reads it (D44). -/
def fpEntryKeys (key : Name) : List String :=
  ["decl", "statement", "digest", "evidence", "approved", "attestation"] ++
    (if key == `entry then ["source", "kind"] else [])

/-- Read `[[key]]` entries from a budget or pin file. Unknown keys are errors (data format v0). -/
def loadFpEntries (path : System.FilePath) (key : Name) (format : String) :
    IO (Except String (Array FpEntry)) := do
  match ← loadTomlFile path with
  | .error e => return .error e
  | .ok t =>
    let (entries, errs) := (do
      checkKeys t ["format", key.toString] path.toString
      match ← getStr t `format path.toString with
      | some f => if f != format then cfgErr s!"{path}: format '{f}', expected {format}"
      | none => cfgErr s!"{path}: missing format"
      let mut out := #[]
      for e in ← getTables t key path.toString do
        let ctx := s!"{path}.{key}"
        checkKeys e (fpEntryKeys key) ctx
        let d := ((← getStr e `decl ctx).getD "").toName
        let s := (← getStr e `statement ctx).getD ""
        let g := (← getStr e `digest ctx).getD ""
        let ev := (← getStr e `evidence ctx).getD ""
        let att := (← getStr e `attestation ctx).getD ""
        let _ ← getStr e `approved ctx
        for k in [`source, `kind] do let _ ← getStr e k ctx
        if d.isAnonymous || s.isEmpty then cfgErr s!"{path}: an entry lacks decl or statement"
        out := out.push { decl := d, statement := s, digest := g, evidence := ev, attestation := att }
      return out : CfgM (Array FpEntry)).run #[]
    if errs.isEmpty then return .ok entries else return .error (String.intercalate "; " errs.toList)

/-- The declaration a stand-in plays (fault fixtures only), or the declaration itself. -/
def targetOf (cfg : Config) (n : Name) : Name :=
  match cfg.standIns.find? (·.decl == n) with
  | some s => s.target
  | none => n

/-- The declaration checked in place of a pinned or budgeted one. -/
def checkedFor (cfg : Config) (target : Name) : Name :=
  match cfg.standIns.find? (·.target == target) with
  | some s => s.decl
  | none => target

/-- Constants reachable from the project's own constants with proofs, through every
non-background module: the region in which every `sorry` is traced to its source (Tau Ceti
included, Q13). Returns the region and the background constants at its border. -/
def traceRegion (cfg : Config) (env : Environment) : NameSet × NameSet := Id.run do
  let mut stack := projectConsts cfg env
  let mut seen : NameSet := {}
  let mut border : NameSet := {}
  while !stack.isEmpty do
    let n := stack.back!
    stack := stack.pop
    if seen.contains n || border.contains n then continue
    let some m := moduleOf? env n | continue
    if classify cfg m == .background then
      border := border.insert n
      continue
    seen := seen.insert n
    let some info := env.find? n | continue
    for d in depsOf info (proofs := true) do
      unless seen.contains d do stack := stack.push d
  return (seen, border)

def runAxioms (cfg : Config) (env : Environment) (budget : Array FpEntry) (budgetMissing : Bool)
    (fp : Fingerprinter) : Report × Fingerprinter := Id.run do
  let mut fp := fp
  let mut diags : Array Diag := #[]
  let allowed := cfg.allowedAxioms
  let consts := projectConsts cfg env
  -- 1. project axioms, 2. non-standard axioms (full view)
  let (region, border) := traceRegion cfg env
  -- one state run for the whole audit: re-entering `StateM` per constant copied the memo
  let ((projAx, borderSorry), full) := (do
      let mut pa := #[]
      for n in consts do pa := pa.push (n, ← axiomsOf env true n)
      let mut bs := #[]
      for b in border do
        if b != ``sorryAx && (← axiomsOf env true b).contains ``sorryAx then bs := bs.push b
      return (pa, bs) : StateM AxState _).run {}
  for (n, ax) in projAx do
    let some info := env.find? n | continue
    if info matches .axiomInfo _ then
      diags := diags.push { check := "axioms", code := "project-axiom", decl := n, modName := moduleOf? env n, message := s!"project axiom {n}" }
    for a in ax do
      unless allowed.contains a || a == ``sorryAx || a == n do
        diags := diags.push { check := "axioms", code := "nonstandard-axiom", decl := n, message := s!"{n} depends on the non-standard axiom {a}" }
  -- 3. trace every sorry to its source
  for b in borderSorry do
    do
      diags := diags.push { check := "axioms", code := "sorry-in-background", decl := b, message := s!"background constant {b} depends on sorryAx" }
  let mut sources : NameSet := {}
  for n in region do
    if let some info := env.find? n then
      if directSorry info then sources := sources.insert (ownerOf env n)
  -- 4. the budget, bound to statement fingerprints
  if budgetMissing && !sources.isEmpty then
    diags := diags.push { check := "axioms", code := "budget-missing", message := "no sorry budget file, but sorry sources exist" }
  let mut used : NameSet := {}
  let mut srcJson := #[]
  for s in sortNames sources.toArray do
    let target := targetOf cfg s
    let (stmt, fp1) := fp.statement s
    let (dig, fp2) := fp1.digest s
    fp := fp2
    let inProject := isProjectConst cfg env s
    match budget.find? (·.decl == target) with
    | none =>
      diags := diags.push { check := "axioms", code := "sorry-not-in-budget", decl := s, modName := moduleOf? env s, message := s!"sorry source {s} is not in the sorry budget" }
      srcJson := srcJson.push (Json.mkObj [("decl", nameJson s), ("budgeted", Json.bool false),
        ("statement", Json.str stmt), ("digest", Json.str dig)])
    | some e =>
      used := used.insert target
      if e.statement != stmt then
        diags := diags.push { check := "axioms", code := "budget-statement-mismatch", decl := s, message := s!"{s}: its statement changed since the budget entry for {target} was approved (budget {e.statement}, now {stmt})" }
      else if !e.digest.isEmpty && e.digest != dig then
        diags := diags.push { check := "axioms", code := "budget-digest-stale", decl := s, severity := .warning, message := s!"{s}: a definition its statement uses changed; the budget entry is stale (re-review)" }
      srcJson := srcJson.push (Json.mkObj [("decl", nameJson s), ("budgeted", Json.bool true),
        ("in_project", Json.bool inProject), ("statement", Json.str stmt), ("digest", Json.str dig)])
  for e in budget do
    unless used.contains e.decl do
      diags := diags.push { check := "axioms", code := "budget-entry-unused", decl := e.decl, severity := .warning, message := s!"budget entry {e.decl} matches no sorry source; remove it" }
  -- 5. per-declaration traces for the abstract (statement level | with proofs)
  let mut roots := (constsOfModule env cfg.abstract).filter (isUserFacing env)
  roots := sortNames roots
  if cfg.reverseRoots then roots := roots.reverse
  let (stmtSets, stmtSt) := (roots.mapM (axiomsOf env false) : StateM AxState _).run {}
  let (fullSets, fullSt) := (roots.mapM (axiomsOf env true) : StateM AxState _).run {}
  let mut traces := #[]
  for i in [0:roots.size] do
    let n := roots[i]!
    let s := stmtSets[i]!
    let f := fullSets[i]!
    let ph := (placeholderKind? env n).map Json.str |>.getD Json.null
    traces := traces.push (Json.mkObj [("decl", nameJson n),
      ("statement_level", Json.arr ((sortNames s.toArray).map nameJson)),
      ("with_proofs", Json.arr ((sortNames f.toArray).map nameJson)), ("placeholder", ph)])
  let cycles := full.cycles ++ stmtSt.cycles ++ fullSt.cycles
  for c in cycles do
    diags := diags.push { check := "axioms", code := "checker-cycle", decl := c, message := s!"unexpected dependency cycle at {c}: the axiom set may be incomplete" }
  -- 6. debt counts
  let placeholders := (consts.filter fun n => placeholderAttr.hasTag env n).size
  let proofDebt := (sources.toArray.filter fun s => budget.any (·.decl == targetOf cfg s)).size
  ({ check := "axioms", diags,
     data := [("allowed", Json.arr (allowed.map nameJson)), ("trace_region", toJson region.size),
       ("sorry_sources", Json.arr srcJson),
       ("debt", Json.mkObj [("placeholder_debt", toJson placeholders), ("proof_debt", toJson proofDebt),
         ("copied_proof_debt", toJson (0 : Nat)),
         ("copied_proof_debt_note", Json.str "no copy record: the copy tool (K15) is not built")]),
       ("traces", Json.arr traces)] }, fp)

/-- TOML text for budget entries of the current sorry sources (for the owner to approve). -/
def suggestBudget (cfg : Config) (env : Environment) (fp : Fingerprinter) : String := Id.run do
  let (region, _) := traceRegion cfg env
  let mut sources : NameSet := {}
  for n in region do
    if let some info := env.find? n then
      if directSorry info then sources := sources.insert (ownerOf env n)
  let mut fp := fp
  let mut out := ""
  for s in sortNames sources.toArray do
    let (stmt, fp1) := fp.statement s
    let (dig, fp2) := fp1.digest s
    fp := fp2
    out := out ++ s!"[[entry]]\ndecl = \"{s}\"\nstatement = \"{stmt}\"\ndigest = \"{dig}\"\n" ++
      s!"evidence = \"{evidenceId s stmt dig}\"\n\n"
  out

/-! ## (c) Placeholders: the census (K2) -/

def runPlaceholders (cfg : Config) (env : Environment) : Report := Id.run do
  let consts := (projectConsts cfg env).filter fun n =>
    match moduleOf? env n with
    | some m => anyPrefix cfg.placeholderModules m
    | none => false
  let mut diags := #[]
  let mut tagged : NameSet := {}
  let mut covered : NameSet := {}
  for n in consts do
    if placeholderAttr.hasTag env n then
      tagged := tagged.insert n
      if let some info := env.find? n then
        if info matches .thmInfo _ then
          diags := diags.push { check := "placeholders", code := "placeholder-not-data", decl := n, message := s!"{n} is a theorem tagged @[placeholder]; placeholders are data only" }
        for d in depsOf info (proofs := false) do covered := covered.insert d
  let mut census := #[]
  for n in sortNames consts do
    let some k := placeholderKind? env n | continue
    let isTagged := tagged.contains n
    if k == "sorry-valued def" then
      diags := diags.push { check := "placeholders", code := "placeholder-sorry-valued", decl := n, severity := .warning, message := s!"{n} is sorry-valued; use the opaque NonemptyType pattern" }
    if let some (.opaqueInfo v) := env.find? n then
      if (usedConsts v.value).contains ``sorryAx then
        diags := diags.push { check := "placeholders", code := "placeholder-sorry-witness", decl := n, severity := .warning, message := s!"{n} is an opaque whose witness is sorry; the axiom audit counts it as a sorry source (use the NonemptyType pattern or a real witness)" }
    if !isTagged && !covered.contains n then
      diags := diags.push { check := "placeholders", code := "placeholder-untagged", decl := n, modName := moduleOf? env n, message := s!"{n} ({k}) is a placeholder without @[placeholder] and is not the carrier of a tagged one" }
    let kind := if isTagged then "@[placeholder]" else if covered.contains n then s!"{k} (carrier)" else k
    census := census.push (Json.mkObj [("decl", nameJson n), ("kind", Json.str kind),
      ("module", (moduleOf? env n).map nameJson |>.getD Json.null)])
  { check := "placeholders", diags, data := [("placeholder_count", toJson tagged.size), ("census", Json.arr census)] }

/-! ## (d) Imports: the D10 rule and the Foundations-name warning (Q14) -/

def runImports (cfg : Config) (env : Environment) : Report := Id.run do
  let mut diags := #[]
  let mut checked := 0
  for i in [0:env.header.moduleNames.size] do
    let m := env.header.moduleNames[i]!
    unless isProjectModule cfg m do continue
    checked := checked + 1
    match cfg.importRules.find? (fun r => r.module.isPrefixOf m) with
    | none =>
      diags := diags.push { check := "imports", code := "import-no-rule", modName := m, message := s!"no import rule covers module {m}" }
    | some r =>
      for imp in env.header.moduleData[i]!.imports do
        unless anyPrefix r.allow imp.module do
          diags := diags.push { check := "imports", code := "import-not-allowed", modName := m, decl := imp.module, message := s!"{m} imports {imp.module} (rule {r.module} allows {r.allow})" }
  -- Foundations-name warning
  let mut reExported : NameSet := {}
  for i in [0:env.header.moduleNames.size] do
    let m := env.header.moduleNames[i]!
    unless anyPrefix cfg.fnFoundations m do continue
    for c in env.header.moduleData[i]!.constNames do
      if let some (.defnInfo v) := env.find? c then
        if v.hints.isAbbrev then
          for d in usedConsts v.value do reExported := reExported.insert d
  let mut users : Std.HashMap Name (Array Name) := {}
  for c in (constsOfModule env cfg.abstract).filter (isUserFacing env) do
    let some info := env.find? c | continue
    for d in depsOf info (proofs := false) do
      let some dm := moduleOf? env d | continue
      unless anyPrefix cfg.fnChecked dm do continue
      -- a direct occurrence bypasses Foundations, even when Foundations re-exports the object
      if cfg.fnRequired.contains d || !anyPrefix cfg.fnBasic d then
        users := users.insert d ((users.getD d #[]).push c)
  for (d, us) in users.toArray.qsort (fun a b => a.1.toString < b.1.toString) do
    let req := cfg.fnRequired.contains d
    let shown := (sortNames us).toList.take 3
    diags := diags.push { check := "imports", code := if req then "foundations-name-required" else "foundations-name", severity := .warning, decl := d, message := (if req then s!"the abstract uses the substantial object {d} directly; use its Foundations name" else if reExported.contains d then s!"the abstract uses {d} directly although Foundations re-exports it; use the Foundations name" else s!"the abstract uses {d} directly; it is neither basic vocabulary nor reached through a Foundations name") ++ s!" (used by {shown}{if us.size > 3 then " ..." else ""})" }
  { check := "imports", diags, data := [("modules_checked", toJson checked)] }

/-! ## (e) The statement pin (D40 Finding 3, D43/F10) -/

def runPin (cfg : Config) (env : Environment) (pins : Array FpEntry) (fp : Fingerprinter) :
    Report × Fingerprinter := Id.run do
  let mut fp := fp
  let mut diags := #[]
  let mut out := #[]
  for p in cfg.pinDecls do
    let d := checkedFor cfg p
    match pins.find? (·.decl == p) with
    | none =>
      diags := diags.push { check := "pin", code := "pin-entry-missing", decl := p, message := s!"{p} is to be pinned but the pin file has no entry for it" }
    | some e =>
      if (env.find? d).isNone then
        diags := diags.push { check := "pin", code := "pin-decl-missing", decl := d, message := s!"pinned declaration {d} does not exist" }
        continue
      let (stmt, fp1) := fp.statement d
      let (dig, fp2) := fp1.digest d
      fp := fp2
      let stmtOk := e.statement == stmt
      let digOk := e.digest == dig
      unless stmtOk do
        diags := diags.push { check := "pin", code := "pin-statement-mismatch", decl := d, message := s!"the own statement of {d} differs from the hard pin of {p} (pinned {e.statement}, now {stmt}); the owner must re-approve" }
      if stmtOk && !digOk then
        diags := diags.push { check := "pin", code := "pin-digest-stale", decl := d, severity := .warning, message := s!"a definition used by {d} changed (transitive digest); dependent reviews are stale and re-review is queued" }
      out := out.push (Json.mkObj [("decl", nameJson p), ("checked", nameJson d),
        ("statement", Json.str stmt), ("statement_ok", Json.bool stmtOk),
        ("digest", Json.str dig), ("digest_ok", Json.bool digOk)])
  for e in pins do
    unless cfg.pinDecls.contains e.decl do
      diags := diags.push { check := "pin", code := "pin-entry-unknown", decl := e.decl, severity := .warning, message := s!"pin entry {e.decl} is not in the config's pin.decls" }
  ({ check := "pin", diags, data := [("pins", Json.arr out)] }, fp)

def suggestPins (cfg : Config) (fp : Fingerprinter) : String := Id.run do
  let mut fp := fp
  let mut out := ""
  for p in cfg.pinDecls do
    let (stmt, fp1) := fp.statement p
    let (dig, fp2) := fp1.digest p
    fp := fp2
    out := out ++ s!"[[pin]]\ndecl = \"{p}\"\nstatement = \"{stmt}\"\ndigest = \"{dig}\"\n" ++
      s!"evidence = \"{evidenceId p stmt dig}\"\n\n"
  out

/-! ## (f) Impact: reverse dependencies, proofs included (brainstorm §7.3) -/

def runImpact (cfg : Config) (env : Environment) (starts : Array Name) : Report := Id.run do
  let (region, _) := traceRegion cfg env
  let mut rev : Std.HashMap Name (Array Name) := {}
  for n in region do
    if let some info := env.find? n then
      for d in depsOf info (proofs := true) do
        if d != n then rev := rev.insert d ((rev.getD d #[]).push n)
  let mut diags := #[]
  let mut results := #[]
  for s in starts do
    if (env.find? s).isNone then
      diags := diags.push { check := "impact", code := "impact-unknown-decl", decl := s, message := s!"unknown declaration {s}" }
      continue
    let mut seen : NameSet := {}
    let mut todo := #[s]
    while !todo.isEmpty do
      let n := todo.back!
      todo := todo.pop
      for u in rev.getD n #[] do
        unless seen.contains u do
          seen := seen.insert u
          todo := todo.push u
    let shown := sortNames (seen.toArray.filter fun n => isUserFacing env n && isProjectConst cfg env n)
    results := results.push (Json.mkObj [("decl", nameJson s), ("impacted_total", toJson seen.size),
      ("impacted_project", Json.arr (shown.map nameJson))])
  { check := "impact", diags, data := [("impact", Json.arr results)] }

end ReviewKit
