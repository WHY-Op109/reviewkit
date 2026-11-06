/-
ReviewKit: the `review` command line (K1). All output is JSON (format `reviewkit-report/0`).

  lake exe review --config <project.toml> [--import Module]... <command> [args]

Commands
  all                      closure, axioms, placeholders, imports, pin, evidence, attestations
  closure                  the key-definition list; fails on project code outside the classes
  axioms [--toml]          scoped audit; --toml prints budget entries for the current sources
  placeholders             census; fails on untagged placeholders
  imports                  D10 import rule; Foundations-name warnings
  pin [--toml]             statement pin; --toml prints pin entries for the owner to approve
  evidence                 evidence records (integrity) and the budget's and pin's links to them
  attestations             computed staleness of every attestation (output only, D43/F11)
  record [--closure] [D...]  write fingerprint evidence records (default: pinned and budgeted
                           declarations; --closure adds the key-definition list)
  attest --kind K --by B --initials I --date YYYY-MM-DD --scope S [--id ID] D...
                           write the evidence for D... and print an attestation block (TOML)
  fingerprint D... [--text]  statement fingerprint and transitive digest of declarations
  impact D...              reverse dependencies (proofs included)
  faults <faults.toml>     run the fault fixtures against their expected diagnostics
  selftest                 SHA-256 test vectors

Global options: --config FILE (required), --import MODULE (repeatable), --evidence-dir DIR and
--attestations FILE (override the config's evidence.dir and attestations.file).

Exit status: 0 no errors, 1 errors found (or a fixture not matched), 2 setup failure.
-/
import Lean
import ReviewKit.Evidence

open Lean ReviewKit

namespace ReviewKit.CLI

def envelope (cmd : String) (cfgPath : String) (ok : Bool) (body : List (String × Json)) : Json :=
  Json.mkObj ([("format", Json.str "reviewkit-report/0"), ("command", Json.str cmd),
    ("config", Json.str cfgPath), ("ok", Json.bool ok)] ++ body)

initialize initDone : IO.Ref Bool ← IO.mkRef false

def importEnv (mods : Array Name) : IO Environment := do
  unless ← initDone.get do
    initSearchPath (← findSysroot)
    initDone.set true
  -- one-shot: `importModules` consumes it, so it is set before every import
  unsafe enableInitializersExecution
  importModules (mods.map fun m => { module := m }) {} (trustLevel := 1024) (loadExts := true)

def loadEntries (path? : Option System.FilePath) (key : Name) (format : String) :
    IO (Except String (Array FpEntry × Bool)) := do
  match path? with
  | none => return .ok (#[], true)
  | some p =>
    unless ← p.pathExists do return .ok (#[], true)
    match ← loadFpEntries p key format with
    | .ok es => return .ok (es, false)
    | .error e => return .error e

/-- Run one check command on an environment. -/
def runCheck (cfg : Config) (env : Environment) (cmd : String) (args : List String) :
    IO (Except String (Array Report)) := do
  let fp : Fingerprinter := { env, cfg }
  let budget? ← loadEntries cfg.budgetFile `entry "reviewkit-sorry-budget/0"
  let pins? ← loadEntries cfg.pinFile `pin "reviewkit-statement-pin/0"
  let axiomsR : IO (Except String Report) := do
    match budget? with
    | .error e => return .error e
    | .ok (b, missing) => return .ok (runAxioms cfg env b missing fp).1
  let pinR : IO (Except String Report) := do
    match pins? with
    | .error e => return .error e
    | .ok (p, missing) =>
      if missing && !cfg.pinDecls.isEmpty then
        return .ok { check := "pin", diags := #[{ check := "pin", code := "pin-file-missing", message := s!"pin file {cfg.pinFile} not found" }] }
      return .ok (runPin cfg env p fp).1
  let atts ← loadAttestations cfg.attestationsFile
  let (recs, recDiags) ← match cfg.evidenceDir with
    | some d => loadRecords d
    | none => pure (#[], #[])
  let evidenceR : IO (Except String Report) := do
    match budget?, pins? with
    | .ok (b, _), .ok (p, _) => return .ok (runEvidence cfg env recs recDiags b p atts fp).1
    | .error e, _ | _, .error e => return .error e
  let attestationsR : Report := (runAttestations env recs atts fp).1
  match cmd with
  | "closure" => return .ok #[runClosure cfg env]
  | "axioms" => return (← axiomsR).map (#[·])
  | "placeholders" => return .ok #[runPlaceholders cfg env]
  | "imports" => return .ok #[runImports cfg env]
  | "pin" => return (← pinR).map (#[·])
  | "evidence" => return (← evidenceR).map (#[·])
  | "attestations" => return .ok #[attestationsR]
  | "impact" => return .ok #[runImpact cfg env (args.filter (!·.startsWith "--") |>.toArray.map String.toName)]
  | "all" =>
    let a ← axiomsR
    let p ← pinR
    let e ← evidenceR
    match a, p, e with
    | .ok a, .ok p, .ok e =>
      return .ok #[runClosure cfg env, a, runPlaceholders cfg env, runImports cfg env, p, e, attestationsR]
    | .error e, _, _ | _, .error e, _ | _, _, .error e => return .error e
  | _ => return .error s!"unknown command {cmd}"

/-! ## Fault fixtures (D27 test 1, D43/F21) -/

structure Expect where
  code : String
  decl : Option Name
  severity : String

structure TraceExpect where
  decl : Name
  contains : Array Name

structure Case where
  name : String
  kind : String
  modules : Array Name
  command : String
  args : List String
  abstract : Option Name
  budget : Option String
  pin : Option String
  extraClasses : Array ClosureClass
  standIns : Array StandIn
  orders : Array String
  expects : Array Expect
  traces : Array TraceExpect
  impacted : Array (Name × Array Name)
  pinDecls : Option (Array Name)
  evidence : Option String
  attestations : Option String
  keys : Array (Name × String)
  /-- `fpcompare` cases: (a, b, level, same). -/
  compares : Array (Name × Name × String × Bool)

def decodeCases (path : System.FilePath) (t : Lake.Toml.Table) : CfgM (Array Case) := do
  checkKeys t ["format", "case"] path.toString
  match ← getStr t `format path.toString with
  | some "reviewkit-faults/0" => pure ()
  | _ => cfgErr s!"{path}: format must be reviewkit-faults/0"
  let mut out := #[]
  for c in ← getTables t `case path.toString do
    checkKeys c ["name", "kind", "modules", "command", "args", "abstract", "budget", "pin",
      "class", "stand_in", "orders", "expect", "trace", "impact", "pin_decls", "key", "compare",
      "evidence", "attestations"] "case"
    let name := (← getStr c `name "case").getD "?"
    let ctx := s!"case {name}"
    let kind := (← getStr c `kind ctx).getD "fault"
    unless kind == "fault" || kind == "control" || kind == "fpcompare" do
      cfgErr s!"{ctx}: kind must be fault, control or fpcompare"
    let mut classes := #[]
    for ct in ← getTables c `class ctx do
      classes := classes.push { tag := (← getStr ct `tag ctx).getD "", modules := ← getNames ct `modules ctx }
    let mut standIns := #[]
    for st in ← getTables c `stand_in ctx do
      standIns := standIns.push { decl := ((← getStr st `decl ctx).getD "").toName, target := ((← getStr st `for ctx).getD "").toName }
    let mut expects := #[]
    for e in ← getTables c `expect ctx do
      checkKeys e ["code", "decl", "severity"] s!"{ctx}.expect"
      expects := expects.push { code := (← getStr e `code ctx).getD "", decl := (← getStr e `decl ctx).map String.toName, severity := (← getStr e `severity ctx).getD "error" }
    let mut traces := #[]
    for e in ← getTables c `trace ctx do
      traces := traces.push { decl := ((← getStr e `decl ctx).getD "").toName, contains := ← getNames e `contains ctx }
    let mut impacted := #[]
    for e in ← getTables c `impact ctx do
      impacted := impacted.push (((← getStr e `decl ctx).getD "").toName, ← getNames e `includes ctx)
    let pinDecls := (← getNames c `pin_decls ctx)
    let pinDecls := if c.contains `pin_decls then some pinDecls else none
    let mut keys := #[]
    for e in ← getTables c `key ctx do
      keys := keys.push (((← getStr e `decl ctx).getD "").toName, (← getStr e `tag ctx).getD "")
    let mut compares := #[]
    for e in ← getTables c `compare ctx do
      checkKeys e ["a", "b", "level", "same"] s!"{ctx}.compare"
      compares := compares.push (((← getStr e `a ctx).getD "").toName, ((← getStr e `b ctx).getD "").toName,
        (← getStr e `level ctx).getD "digest", (← getBool e `same ctx).getD false)
    if kind == "fault" && expects.isEmpty then cfgErr s!"{ctx}: a fault needs at least one expect"
    let orders ← getStrs c `orders ctx
    let modules ← getNames c `modules ctx
    let command := (← getStr c `command ctx).getD "all"
    let args := (← getStrs c `args ctx).toList
    let abstract := (← getStr c `abstract ctx).map String.toName
    let budget ← getStr c `budget ctx
    let pin ← getStr c `pin ctx
    let evidence ← getStr c `evidence ctx
    let attestations ← getStr c `attestations ctx
    let orders := if orders.isEmpty then #["forward"] else orders
    out := out.push { name, kind, modules, command, args, abstract, budget, pin,
                      extraClasses := classes, standIns, orders, expects, traces, impacted,
                      pinDecls, evidence, attestations, keys, compares }
  return out

def diagMatches (e : Expect) (d : Diag) : Bool :=
  d.code == e.code && d.severity.toString == e.severity &&
    (match e.decl with | some n => d.decl == some n | none => true)

/-- Run one case in one root order: returns failures (empty = the case behaves as expected). -/
def runCase (base : Config) (c : Case) (order : String) : IO (Array String × Json) := do
  let cfg := { base with
    abstract := c.abstract.getD base.abstract
    budgetFile := (c.budget.map System.FilePath.mk).orElse fun _ => base.budgetFile
    pinFile := (c.pin.map System.FilePath.mk).orElse fun _ => base.pinFile
    evidenceDir := (c.evidence.map System.FilePath.mk).orElse fun _ => base.evidenceDir
    attestationsFile := (c.attestations.map System.FilePath.mk).orElse fun _ => base.attestationsFile
    -- an overriding abstract module is classed `abstract` like the configured one
    classes := (match c.abstract with
      | some m => #[({ tag := "abstract", modules := #[m] } : ClosureClass)]
      | none => #[]) ++ c.extraClasses ++ base.classes
    standIns := c.standIns
    reverseRoots := order == "reverse"
    pinDecls := c.pinDecls.getD base.pinDecls }
  let env ← try importEnv (base.imports ++ c.modules)
    catch e => return (#[s!"setup error: import failed: {e}"], Json.null)
  if c.kind == "fpcompare" then
    let mut fp : Fingerprinter := { env, cfg }
    let mut fails := #[]
    let mut rows := #[]
    for (a, b, level, same) in c.compares do
      if (env.find? a).isNone || (env.find? b).isNone then
        fails := fails.push s!"setup error: unknown declaration {a} or {b}"; continue
      let (ha, fp1) := if level == "statement" then fp.statement a else fp.digest a
      let (hb, fp2) := if level == "statement" then fp1.statement b else fp1.digest b
      fp := fp2
      let ok := (ha == hb) == same
      unless ok do
        fails := fails.push s!"{level} of {a} and {b}: {if ha == hb then "same" else "differ"}, wanted {if same then "same" else "differ"}"
      rows := rows.push (Json.mkObj [("a", nameJson a), ("b", nameJson b), ("level", Json.str level),
        ("same", Json.bool (ha == hb)), ("wanted_same", Json.bool same)])
    return (fails, Json.arr rows)
  match ← runCheck cfg env c.command c.args with
  | .error e => return (#[s!"setup error: {e}"], Json.null)
  | .ok reports =>
    let diags := reports.foldl (fun acc r => acc ++ r.diags) #[]
    let errors := diags.filter (·.severity == .error)
    let mut fails := #[]
    for e in c.expects do
      unless diags.any (diagMatches e) do
        fails := fails.push s!"expected diagnostic not produced: {e.code}{(e.decl.map (s!" at {·}")).getD ""} ({e.severity})"
    if c.kind == "control" then
      for d in errors do
        unless c.expects.any (diagMatches · d) do
          fails := fails.push s!"control produced an error: {d.code} {d.message}"
    else
      let wantsError := c.expects.any (·.severity == "error")
      if wantsError && errors.isEmpty then fails := fails.push "fault produced no error"
      -- every error must be one the fixture expects: no unrelated breakage passes for a catch
      for d in errors do
        unless c.expects.any (diagMatches · d) do
          fails := fails.push s!"unexpected error: {d.code} {(d.decl.map toString).getD ""}: {d.message}"
    for t in c.traces do
      let found := reports.findSome? fun r => (r.data.lookup "traces").bind fun j =>
        match j with
        | .arr xs => xs.find? fun x => x.getObjValD "decl" == Json.str t.decl.toString
        | _ => none
      match found with
      | none => fails := fails.push s!"no trace for {t.decl}"
      | some x =>
        let wp := x.getObjValD "with_proofs"
        for a in t.contains do
          let has := match wp with | .arr ys => ys.contains (Json.str a.toString) | _ => false
          unless has do fails := fails.push s!"trace of {t.decl} lacks {a} (order {order})"
    for (d, tag) in c.keys do
      let found := reports.findSome? fun r => (r.data.lookup "key_list").bind fun j =>
        match j with
        | .arr xs => xs.find? fun x => x.getObjValD "decl" == Json.str d.toString
        | _ => none
      match found with
      | none => fails := fails.push s!"{d} is not in the key-definition list"
      | some x => unless x.getObjValD "tag" == Json.str tag do
          fails := fails.push s!"{d} has tag {x.getObjValD "tag"}, wanted {tag}"
    for (d, incl) in c.impacted do
      let found := reports.findSome? fun r => (r.data.lookup "impact").bind fun j =>
        match j with
        | .arr xs => xs.find? fun x => x.getObjValD "decl" == Json.str d.toString
        | _ => none
      match found with
      | none => fails := fails.push s!"no impact result for {d}"
      | some x =>
        for i in incl do
          let has := match x.getObjValD "impacted_project" with
            | .arr ys => ys.contains (Json.str i.toString) | _ => false
          unless has do fails := fails.push s!"impact of {d} lacks {i}"
    return (fails, Json.arr (diags.map Diag.toJson))

def loadCases (path : System.FilePath) : IO (Except String (Array Case)) := do
  match ← loadTomlFile path with
  | .error e => return .error e
  | .ok t =>
    let (cases, errs) := (decodeCases path t).run #[]
    if errs.isEmpty then return .ok cases else return .error (String.intercalate "; " errs.toList)

/-- Run one case in this process (the child side of `faults`). -/
def runOneCase (base : Config) (path : System.FilePath) (idx : Nat) (order : String) : IO Json := do
  match ← loadCases path with
  | .error e => return Json.mkObj [("failures", Json.arr #[Json.str s!"setup error: {e}"])]
  | .ok cases =>
    let some c := cases[idx]? | return Json.mkObj [("failures", Json.arr #[Json.str "setup error: no such case"])]
    let (fails, diags) ← runCase base c order
    return Json.mkObj [("failures", Json.arr (fails.map Json.str)), ("diagnostics", diags)]

/-- Run every case, each in its own child process: the environment of one case (Mathlib and
Tau Ceti) is freed before the next, and a crash fails only that case, as a setup failure. -/
def runFaults (base : Config) (path : System.FilePath) : IO (Bool × Json) := do
  match ← loadCases path with
  | .error e => return (false, Json.mkObj [("setup_error", Json.str e)])
  | .ok cases =>
    let exe ← IO.appPath
    let mut allOk := true
    let mut results := #[]
    for i in [0:cases.size] do
      let some c := cases[i]? | continue
      for order in c.orders do
        let caseArgs := #["--config", base.path.toString, "faults-case", path.toString, toString i, order]
        let out ← IO.Process.output { cmd := exe.toString, args := caseArgs }
        let (fails, diags) : Array String × Json :=
          match Json.parse out.stdout with
          | .ok j =>
            let fs := match j.getObjValD "failures" with
              | .arr xs => xs.filterMap fun x => match x with | .str s => some s | _ => none
              | _ => #["setup error: malformed case output"]
            (fs, j.getObjValD "diagnostics")
          | .error _ =>
            (#[s!"setup error: case process exited with {out.exitCode}: {out.stderr.take 400}"], Json.null)
        let ok := fails.isEmpty
        allOk := allOk && ok
        results := results.push (Json.mkObj [("case", Json.str c.name), ("kind", Json.str c.kind),
          ("order", Json.str order), ("passed", Json.bool ok),
          ("failures", Json.arr (fails.map Json.str)), ("diagnostics", diags)])
    let nf := (cases.filter (·.kind != "control")).size
    let nc := (cases.filter (·.kind == "control")).size
    return (allOk, Json.mkObj [("faults", toJson nf), ("controls", toJson nc),
      ("results", Json.arr results)])

end ReviewKit.CLI

open ReviewKit.CLI

def usage : String :=
  "usage: lake exe review --config <project.toml> <command> [args]\n" ++
  "commands: all closure axioms placeholders imports pin evidence attestations record attest fingerprint impact faults selftest"

def main (argv : List String) : IO UInt32 := do
  let out (j : Json) : IO Unit := IO.println j.pretty
  let rec split (xs : List String) (cfg : Option String) (imps rest : List String)
      (ev at_ : Option String) :
      Option String × List String × List String × Option String × Option String :=
    match xs with
    | "--config" :: p :: tl => split tl (some p) imps rest ev at_
    | "--import" :: m :: tl => split tl cfg (imps ++ [m]) rest ev at_
    | "--evidence-dir" :: d :: tl => split tl cfg imps rest (some d) at_
    | "--attestations" :: f :: tl => split tl cfg imps rest ev (some f)
    | x :: tl => split tl cfg imps (rest ++ [x]) ev at_
    | [] => (cfg, imps, rest, ev, at_)
  let (cfgPath?, extraImports, rest, evOverride, attOverride) := split argv none [] [] none none
  let cfgPath? := cfgPath?.orElse fun _ => none
  let cmd := rest.headD "help"
  let args := rest.drop 1
  if cmd == "selftest" then
    let ok := ReviewKit.Sha256.selfTest
    out (envelope cmd "" ok [("sha256_vectors", Json.bool ok)])
    return if ok then 0 else 1
  let some cfgPath := cfgPath? | do
    IO.eprintln usage
    out (envelope cmd "" false [("setup_error", Json.str "missing --config <file>")])
    return 2
  let cfg ← match ← loadConfig cfgPath with
    | .ok c => pure { c with
        imports := c.imports ++ extraImports.toArray.map String.toName
        evidenceDir := (evOverride.map System.FilePath.mk).orElse fun _ => c.evidenceDir
        attestationsFile := (attOverride.map System.FilePath.mk).orElse fun _ => c.attestationsFile }
    | .error e => do
      out (envelope cmd cfgPath false [("setup_error", Json.str e)])
      return 2
  if cmd == "faults-case" then
    match args with
    | [file, idx, order] =>
      IO.println (← runOneCase cfg file idx.toNat! order).compress
      return 0
    | _ => return 2
  if cmd == "faults" then
    let some file := args.head? | do
      out (envelope cmd cfgPath false [("setup_error", Json.str "faults needs a file")]); return 2
    let (ok, j) ← runFaults cfg file
    out (envelope cmd cfgPath ok [("faults", j)])
    return if ok then 0 else 1
  let env ← try importEnv cfg.imports catch e => do
    out (envelope cmd cfgPath false [("setup_error", Json.str s!"import failed: {e}")])
    return 2
  let toml := args.contains "--toml"
  if cmd == "fingerprint" then
    let decls := args.filter (!·.startsWith "--")
    let mut fp : Fingerprinter := { env, cfg }
    let mut rows := #[]
    let mut ok := true
    for d in decls do
      let n := d.toName
      if (env.find? n).isNone then
        ok := false
        rows := rows.push (Json.mkObj [("decl", Json.str d), ("error", Json.str "unknown declaration")])
        continue
      let (s, fp1) := fp.statement n
      let (g, fp2) := fp1.digest n
      let (t, fp3) := fp2.text n
      fp := fp3
      rows := rows.push (Json.mkObj ([("decl", Json.str d), ("statement", Json.str s), ("digest", Json.str g)] ++
        (if args.contains "--text" then [("statement_text", Json.str t)] else [])))
    out (envelope cmd cfgPath ok [("fingerprints", Json.arr rows)])
    return if ok then 0 else 1
  if cmd == "record" || cmd == "attest" then
    let valued := ["--kind", "--by", "--initials", "--date", "--scope", "--id"]
    let rec opts (xs : List String) (kv : List (String × String)) (pos : List String) :
        List (String × String) × List String :=
      match xs with
      | k :: v :: tl => if valued.contains k then opts tl (kv ++ [(k, v)]) pos
                        else opts (v :: tl) kv (pos ++ [k])
      | [k] => (kv, pos ++ [k])
      | [] => (kv, pos)
    let (kv, pos) := opts args [] []
    let get (k : String) : String := (kv.lookup k).getD ""
    let some dir := cfg.evidenceDir | do
      out (envelope cmd cfgPath false [("setup_error", Json.str "no evidence directory: set evidence.dir or --evidence-dir")])
      return 2
    let named := (pos.filter (!·.startsWith "--")).map String.toName
    let mut decls : Array Name := named.toArray
    if cmd == "record" && decls.isEmpty then
      decls := cfg.pinDecls
      match ← loadEntries cfg.budgetFile `entry "reviewkit-sorry-budget/0" with
      | .ok (b, _) => for e in b do unless decls.contains e.decl do decls := decls.push e.decl
      | .error e => do
        out (envelope cmd cfgPath false [("setup_error", Json.str e)]); return 2
    if cmd == "record" && pos.contains "--closure" then
      if let some (.arr xs) := (runClosure cfg env).data.lookup "key_list" then
        for x in xs do
          if let .ok d := x.getObjValAs? String "decl" then
            unless decls.contains d.toName do decls := decls.push d.toName
    if cmd == "attest" then
      let missing := ["--kind", "--by", "--initials", "--date", "--scope"].filter (get · |>.isEmpty)
      if !missing.isEmpty || decls.isEmpty then
        IO.eprintln "usage: review attest --kind K --by B --initials I --date YYYY-MM-DD --scope S [--id ID] DECL..."
        out (envelope cmd cfgPath false [("setup_error", Json.str s!"attest needs {missing} and at least one declaration")])
        return 2
      unless attestationKinds.contains (get "--kind") && attestationRoles.contains (get "--by") && isDate (get "--date") do
        out (envelope cmd cfgPath false [("setup_error", Json.str s!"attest: kind must be one of {attestationKinds}, by one of {attestationRoles}, date YYYY-MM-DD")])
        return 2
    let producer : Producer := { config := cfgPath, configSha := ReviewKit.sha (← IO.FS.readFile cfgPath) }
    let mut fp : Fingerprinter := { env, cfg }
    let mut recs := #[]
    let mut rows := #[]
    let mut ok := true
    for d in decls do
      if (env.find? d).isNone then
        ok := false
        rows := rows.push (Json.mkObj [("decl", nameJson d), ("error", Json.str "unknown declaration")])
        continue
      let (r, fp1) := mkRecord env fp d
      fp := fp1
      let status ← writeRecord dir r producer
      recs := recs.push r
      rows := rows.push (Json.mkObj [("decl", nameJson d), ("id", Json.str r.id),
        ("path", Json.str (dir / evidenceRelPath r.decl r.statement r.digest).toString), ("status", Json.str status)])
    if cmd == "attest" then
      unless ok do
        out (envelope cmd cfgPath false [("records", Json.arr rows)]); return 1
      let id := if (get "--id").isEmpty then s!"{get "--date"}-{(decls[0]!).toString}" else get "--id"
      IO.print (attestationToml id (get "--kind") (get "--by") (get "--initials") (get "--date") (get "--scope") recs)
      return 0
    out (envelope cmd cfgPath ok [("records", Json.arr rows)])
    return if ok then 0 else 1
  if toml && cmd == "axioms" then
    IO.print (ReviewKit.suggestBudget cfg env { env, cfg }); return 0
  if toml && cmd == "pin" then
    IO.print (ReviewKit.suggestPins cfg { env, cfg }); return 0
  match ← runCheck cfg env cmd args with
  | .error e =>
    out (envelope cmd cfgPath false [("setup_error", Json.str e)])
    return 2
  | .ok reports =>
    let ok := reports.all (·.ok)
    out (envelope cmd cfgPath ok [("reports", Json.arr (reports.map (·.toJson)))])
    return if ok then 0 else 1
