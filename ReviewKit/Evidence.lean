/-
ReviewKit: machine evidence records and human attestations (D9, D43/F11; data format v0,
`FORMATS.md`).

* `review record` writes one **fingerprint evidence record** per declaration into the evidence
  directory: the statement fingerprint and the transitive digest (SHA-256), each with the
  canonical text it hashes, so a later reader can see what was fingerprinted and diff it.
* `review evidence` checks every record (it parses, its id matches its fingerprints and its
  path, its texts hash to its fingerprints) and the evidence and attestation links of the sorry
  budget and the statement pin.
* `review attestations` computes, for every human attestation in `reviews/`, whether each of its
  bindings is current or stale. The result is output only: it is never written into `reviews/`.
* `review attest` writes the evidence records for some declarations and prints an attestation
  block bound to them, for the owner to append (D9: "a small review command the user runs,
  which stores the fingerprint automatically").
-/
import Lean
import ReviewKit.Checks

open Lean

namespace ReviewKit

/-- The kit's version, recorded in evidence (freshness covers the validation code, D39). -/
def kitVersion : String := "0.2.0"

def evidenceFormat : String := "reviewkit-evidence/0"
def attestationsFormat : String := "reviewkit-attestations/0"

/-! ## Fingerprint records -/

/-- A fingerprint record, as written or as read back. -/
structure FpRecord where
  id : String
  decl : Name
  declKind : String := ""
  module : String := ""
  statement : String
  statementText : String
  digest : String
  digestText : String
  blockIndex : Nat := 0
  blockSize : Nat := 1
  /-- Path relative to the evidence directory (read records only). -/
  relPath : String := ""
  deriving Inhabited

/-- The digest a record's digest text implies (`Fingerprint.lean`, `digestOf`). -/
def FpRecord.impliedDigest (r : FpRecord) : String :=
  if r.blockSize == 1 then sha r.digestText
  else sha s!"M {r.blockIndex} {sha r.digestText}"

/-- Compute the record of a declaration now. -/
def mkRecord (env : Environment) (fp : Fingerprinter) (n : Name) : FpRecord × Fingerprinter :=
  let (stmtText, fp) := fp.text n
  let (dig, fp) := fp.digest n
  let ((digText, idx, size), fp) := fp.digestText n
  let stmt := sha stmtText
  ({ id := evidenceId n stmt dig, decl := n,
     declKind := (env.find? n).map kindOf |>.getD "?",
     module := (moduleOf? env n).map (·.toString) |>.getD "",
     statement := stmt, statementText := stmtText, digest := dig, digestText := digText,
     blockIndex := idx, blockSize := size }, fp)

/-- The provenance block of every record written in one run. -/
structure Producer where
  config : String
  configSha : String

def FpRecord.toJson (r : FpRecord) (p : Producer) : Json := Json.mkObj [
  ("format", Json.str evidenceFormat), ("kind", Json.str "fingerprint"), ("id", Json.str r.id),
  ("decl", Json.str r.decl.toString), ("decl_kind", Json.str r.declKind),
  ("module", Json.str r.module), ("serialisation", Json.str "reviewkit-canonical/0"),
  ("statement", Json.mkObj [("fingerprint", Json.str r.statement), ("text", Json.str r.statementText)]),
  ("digest", Json.mkObj [("fingerprint", Json.str r.digest), ("text", Json.str r.digestText),
    ("block_index", Lean.toJson r.blockIndex), ("block_size", Lean.toJson r.blockSize)]),
  ("producer", Json.mkObj [("tool", Json.str "reviewkit"), ("kit_version", Json.str kitVersion),
    ("lean_version", Json.str Lean.versionString), ("config", Json.str p.config),
    ("config_sha256", Json.str p.configSha)])]

/-- Write one record; returns `created`, `updated` or `unchanged`. -/
def writeRecord (dir : System.FilePath) (r : FpRecord) (p : Producer) : IO String := do
  let path := dir / evidenceRelPath r.decl r.statement r.digest
  let text := (r.toJson p).pretty ++ "\n"
  if ← path.pathExists then
    if (← IO.FS.readFile path) == text then return "unchanged"
    IO.FS.writeFile path text
    return "updated"
  if let some parent := path.parent then IO.FS.createDirAll parent
  IO.FS.writeFile path text
  return "created"

/-- Parse one record file (an error string for a malformed one). -/
def parseRecord (j : Json) : Except String FpRecord := do
  let fmt ← j.getObjValAs? String "format"
  unless fmt == evidenceFormat do throw s!"format '{fmt}', expected {evidenceFormat}"
  let kind ← j.getObjValAs? String "kind"
  unless kind == "fingerprint" do throw s!"kind '{kind}': only fingerprint records exist in v0"
  let st ← j.getObjVal? "statement"
  let dg ← j.getObjVal? "digest"
  return {
    id := ← j.getObjValAs? String "id"
    decl := (← j.getObjValAs? String "decl").toName
    declKind := (j.getObjValAs? String "decl_kind").toOption.getD ""
    module := (j.getObjValAs? String "module").toOption.getD ""
    statement := ← st.getObjValAs? String "fingerprint"
    statementText := ← st.getObjValAs? String "text"
    digest := ← dg.getObjValAs? String "fingerprint"
    digestText := ← dg.getObjValAs? String "text"
    blockIndex := ← dg.getObjValAs? Nat "block_index"
    blockSize := ← dg.getObjValAs? Nat "block_size" }

/-- Every `.json` file under the evidence directory, parsed (malformed ones as diagnostics). -/
def loadRecords (dir : System.FilePath) : IO (Array FpRecord × Array Diag) := do
  unless ← dir.pathExists do return (#[], #[])
  let files ← dir.walkDir
  let mut recs := #[]
  let mut diags := #[]
  let dirStr := dir.toString
  for f in files.qsort (·.toString < ·.toString) do
    unless f.extension == some "json" do continue
    let rel := ((f.toString.drop (dirStr.length + 1)).toString)
    let content ← IO.FS.readFile f
    match Json.parse content >>= parseRecord with
    | .ok r => recs := recs.push { r with relPath := rel }
    | .error e =>
      diags := diags.push { check := "evidence", code := "evidence-format", message := s!"{f}: {e}" }
  return (recs, diags)

/-! ## Attestations (`reviews/`, human-owned) -/

structure Binding where
  decl : Name
  statement : String
  digest : String := ""
  evidence : Array String := #[]
  deriving Inhabited

structure Attestation where
  id : String
  kind : String
  by_ : String
  initials : String
  date : String
  scope : String
  bindings : Array Binding
  deriving Inhabited

def attestationKinds : List String := ["provisional-baseline", "owner-check", "expert-review"]
def attestationRoles : List String := ["owner", "expert"]

def isFingerprint (s : String) : Bool :=
  let h := (s.drop 7).toString
  s.startsWith "sha256:" && h.length == 64 && h.all fun c => c.isDigit || ('a' ≤ c && c ≤ 'f')

def isDate (s : String) : Bool :=
  match s.splitOn "-" with
  | [y, m, d] => y.length == 4 && m.length == 2 && d.length == 2 && (y ++ m ++ d).all Char.isDigit
  | _ => false

/-- Read the attestation file. A missing file is no attestations. -/
def loadAttestations (path? : Option System.FilePath) : IO (Except String (Array Attestation)) := do
  let some path := path? | return .ok #[]
  unless ← path.pathExists do return .ok #[]
  match ← loadTomlFile path with
  | .error e => return .error e
  | .ok t =>
    let (atts, errs) := (do
      checkKeys t ["format", "attestation"] path.toString
      match ← getStr t `format path.toString with
      | some f => if f != attestationsFormat then cfgErr s!"{path}: format '{f}', expected {attestationsFormat}"
      | none => cfgErr s!"{path}: missing format"
      let mut out := #[]
      for a in ← getTables t `attestation path.toString do
        let id := (← getStr a `id s!"{path}.attestation").getD ""
        let ctx := s!"{path}: attestation '{id}'"
        checkKeys a ["id", "kind", "by", "initials", "date", "scope", "binding"] ctx
        let kind := (← getStr a `kind ctx).getD ""
        let by_ := (← getStr a `by ctx).getD ""
        let initials := (← getStr a `initials ctx).getD ""
        let date := (← getStr a `date ctx).getD ""
        let scope := (← getStr a `scope ctx).getD ""
        if id.isEmpty then cfgErr s!"{ctx}: missing id"
        unless attestationKinds.contains kind do cfgErr s!"{ctx}: kind must be one of {attestationKinds}"
        unless attestationRoles.contains by_ do cfgErr s!"{ctx}: by must be one of {attestationRoles}"
        if initials.isEmpty then cfgErr s!"{ctx}: missing initials"
        unless isDate date do cfgErr s!"{ctx}: date must be YYYY-MM-DD"
        if scope.isEmpty then cfgErr s!"{ctx}: missing scope"
        let mut bs := #[]
        for b in ← getTables a `binding ctx do
          checkKeys b ["decl", "statement", "digest", "evidence"] s!"{ctx}.binding"
          let decl := ((← getStr b `decl ctx).getD "").toName
          let stmt := (← getStr b `statement ctx).getD ""
          let dig := (← getStr b `digest ctx).getD ""
          let ev ← getStrs b `evidence ctx
          if decl.isAnonymous then cfgErr s!"{ctx}: a binding lacks decl"
          unless isFingerprint stmt do cfgErr s!"{ctx}: binding {decl}: statement must be a sha256: fingerprint"
          unless dig.isEmpty || isFingerprint dig do cfgErr s!"{ctx}: binding {decl}: digest must be a sha256: fingerprint"
          if ev.isEmpty then cfgErr s!"{ctx}: binding {decl}: at least one evidence id"
          bs := bs.push { decl, statement := stmt, digest := dig, evidence := ev }
        if bs.isEmpty then cfgErr s!"{ctx}: at least one binding"
        out := out.push { id, kind, by_, initials, date, scope, bindings := bs }
      return out : CfgM (Array Attestation)).run #[]
    if errs.isEmpty then return .ok atts else return .error (String.intercalate "; " errs.toList)

/-! ## `review evidence` -/

def runEvidence (cfg : Config) (env : Environment) (recs : Array FpRecord) (loadDiags : Array Diag)
    (budget pins : Array FpEntry) (atts : Except String (Array Attestation)) (fp : Fingerprinter) :
    Report × Fingerprinter := Id.run do
  let mut fp := fp
  let mut diags := loadDiags
  let mut byId : Std.HashMap String FpRecord := {}
  let mut rows := #[]
  for r in recs do
    if byId.contains r.id then
      diags := diags.push { check := "evidence", code := "evidence-duplicate-id", decl := r.decl, message := s!"evidence id {r.id} occurs twice ({r.relPath})" }
    byId := byId.insert r.id r
    let expectId := evidenceId r.decl r.statement r.digest
    let expectPath := (evidenceRelPath r.decl r.statement r.digest).toString
    if r.id != expectId || r.relPath != expectPath then
      diags := diags.push { check := "evidence", code := "evidence-id-mismatch", decl := r.decl, message := s!"{r.relPath}: id {r.id} does not match its fingerprints or path (expected {expectId} at {expectPath})" }
    if sha r.statementText != r.statement then
      diags := diags.push { check := "evidence", code := "evidence-hash-mismatch", decl := r.decl, message := s!"{r.relPath}: the statement text does not hash to {r.statement}" }
    if r.impliedDigest != r.digest then
      diags := diags.push { check := "evidence", code := "evidence-hash-mismatch", decl := r.decl, message := s!"{r.relPath}: the digest text does not give {r.digest}" }
    let mut current := Json.null
    if (env.find? r.decl).isSome then
      let (s, fp1) := fp.statement r.decl
      let (d, fp2) := fp1.digest r.decl
      fp := fp2
      let cur := s == r.statement && d == r.digest
      current := Json.bool cur
      unless cur do
        diags := diags.push { check := "evidence", code := "evidence-outdated", severity := .info, decl := r.decl, message := s!"{r.id} records fingerprints that are no longer current" }
    rows := rows.push (Json.mkObj [("id", Json.str r.id), ("decl", nameJson r.decl),
      ("path", Json.str r.relPath), ("current", current)])
  -- links from the human-owned budget and pin
  let attIds : Array String := match atts with
    | .ok xs => xs.map (·.id)
    | .error _ => #[]
  for (what, entries) in [("budget", budget), ("pin", pins)] do
    for e in entries do
      unless e.evidence.isEmpty do
        match byId[e.evidence]? with
        | none =>
          diags := diags.push { check := "evidence", code := s!"{what}-evidence-missing", decl := e.decl, message := s!"{what} entry {e.decl} names evidence {e.evidence}, which does not exist; run `review record`" }
        | some r =>
          if r.decl != e.decl || r.statement != e.statement || (!e.digest.isEmpty && r.digest != e.digest) then
            diags := diags.push { check := "evidence", code := s!"{what}-evidence-mismatch", decl := e.decl, message := s!"{what} entry {e.decl} and its evidence {e.evidence} disagree on the declaration or its fingerprints" }
      unless e.attestation.isEmpty do
        unless attIds.contains e.attestation do
          diags := diags.push { check := "evidence", code := s!"{what}-attestation-unknown", decl := e.decl, message := s!"{what} entry {e.decl} names attestation {e.attestation}, which does not exist" }
  ({ check := "evidence", diags, data := [("dir", Json.str ((cfg.evidenceDir.map (·.toString)).getD "")),
      ("records", Json.arr rows)] }, fp)

/-! ## `review attestations`: staleness computed from the bindings (D43/F11) -/

/-- Binding states, worst first; the first five are diagnostics. -/
def stateRank : String → Nat
  | "evidence-missing" => 0 | "evidence-mismatch" => 1 | "decl-missing" => 2
  | "stale-statement" => 3 | "stale-digest" => 4 | _ => 5

def runAttestations (env : Environment) (recs : Array FpRecord)
    (attsE : Except String (Array Attestation)) (fp : Fingerprinter) : Report × Fingerprinter := Id.run do
  let mut fp := fp
  if let .error e := attsE then
    return ({ check := "attestations", diags := #[{ check := "attestations", code := "attestation-format", message := e }] }, fp)
  let atts := match attsE with | .ok xs => xs | .error _ => #[]
  let mut byId : Std.HashMap String FpRecord := {}
  for r in recs do byId := byId.insert r.id r
  let mut diags := #[]
  let mut seen : Std.HashSet String := {}
  let mut out := #[]
  for a in atts do
    if seen.contains a.id then
      diags := diags.push { check := "attestations", code := "attestation-duplicate-id", message := s!"attestation id {a.id} is used twice" }
    seen := seen.insert a.id
    let mut worst := "current"
    let mut bjs := #[]
    for b in a.bindings do
      let mut state := "current"
      let mut detail := ""
      for ev in b.evidence do
        match byId[ev]? with
        | none =>
          if stateRank "evidence-missing" < stateRank state then
            state := "evidence-missing"
            detail := s!"evidence {ev} does not exist"
        | some r =>
          let bad := r.decl != b.decl || r.statement != b.statement ||
            (!b.digest.isEmpty && r.digest != b.digest)
          if bad && stateRank "evidence-mismatch" < stateRank state then
            state := "evidence-mismatch"
            detail := s!"evidence {ev} records other fingerprints or another declaration"
      if state == "current" then
        if (env.find? b.decl).isNone then
          state := "decl-missing"
          detail := s!"{b.decl} does not exist (renamed or removed)"
        else
          let (s, fp1) := fp.statement b.decl
          let (d, fp2) := fp1.digest b.decl
          fp := fp2
          if s != b.statement then
            state := "stale-statement"
            detail := s!"the statement of {b.decl} changed since the attestation"
          else if !b.digest.isEmpty && d != b.digest then
            state := "stale-digest"
            detail := s!"a definition {b.decl} uses changed since the attestation (transitive digest)"
      if state != "current" then
        let sev := if stateRank state ≤ 1 then Severity.error else Severity.warning
        diags := diags.push { check := "attestations", code := s!"attestation-{state}", severity := sev, decl := b.decl, message := s!"attestation {a.id}: {detail}" }
      if stateRank state < stateRank worst then worst := state
      bjs := bjs.push (Json.mkObj [("decl", nameJson b.decl), ("state", Json.str state)])
    out := out.push (Json.mkObj [("id", Json.str a.id), ("kind", Json.str a.kind), ("by", Json.str a.by_),
      ("status", Json.str worst), ("bindings", Json.arr bjs)])
  return ({ check := "attestations", diags, data := [("attestations", Json.arr out),
    ("note", Json.str "computed from the bindings; never written into reviews/ (D43/F11)")] }, fp)

/-! ## `review attest`: an attestation block for the owner to append -/

/-- A TOML basic string. -/
def tomlString (s : String) : String := Id.run do
  let mut out := "\""
  for c in s.toList do
    out := out ++ match c with
      | '"' => "\\\"" | '\\' => "\\\\" | '\n' => "\\n" | '\t' => "\\t" | '\r' => "\\r"
      | c => if c.toNat < 32 then s!"\\u{String.ofList (List.replicate (4 - (Nat.toDigits 16 c.toNat).length) '0' ++ Nat.toDigits 16 c.toNat)}" else c.toString
  out ++ "\""

def attestationToml (id kind by_ initials date scope : String) (recs : Array FpRecord) : String := Id.run do
  let mut out := s!"[[attestation]]\nid = {tomlString id}\nkind = {tomlString kind}\nby = {tomlString by_}\n" ++
    s!"initials = {tomlString initials}\ndate = {tomlString date}\nscope = {tomlString scope}\n"
  for r in recs do
    out := out ++ s!"[[attestation.binding]]\ndecl = {tomlString r.decl.toString}\nstatement = {tomlString r.statement}\n" ++
      s!"digest = {tomlString r.digest}\nevidence = [{tomlString r.id}]\n"
  out ++ "\n"

end ReviewKit
