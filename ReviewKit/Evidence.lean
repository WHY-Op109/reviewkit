/-
ReviewKit: fingerprint evidence records (data format v0, `FORMATS.md`).

* `review record` writes one record per declaration into the evidence directory: the statement
  fingerprint and the transitive digest (SHA-256), each with the canonical text it hashes, so a
  later reader can see what was fingerprinted and diff it.
* `review evidence` checks every record (it parses, its id matches its fingerprints and its
  path, its texts hash to its fingerprints) and the evidence links of the sorry budget and the
  statement pin.
-/
import Lean
import ReviewKit.Checks

open Lean

namespace ReviewKit

/-- The kit's version, recorded in evidence records. -/
def kitVersion : String := "0.3.0"

def evidenceFormat : String := "reviewkit-evidence/0"

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

/-! ## `review evidence` -/

def runEvidence (cfg : Config) (env : Environment) (recs : Array FpRecord) (loadDiags : Array Diag)
    (budget pins : Array FpEntry) (fp : Fingerprinter) :
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
  for (what, entries) in [("budget", budget), ("pin", pins)] do
    for e in entries do
      unless e.evidence.isEmpty do
        match byId[e.evidence]? with
        | none =>
          diags := diags.push { check := "evidence", code := s!"{what}-evidence-missing", decl := e.decl, message := s!"{what} entry {e.decl} names evidence {e.evidence}, which does not exist; run `review record`" }
        | some r =>
          if r.decl != e.decl || r.statement != e.statement || (!e.digest.isEmpty && r.digest != e.digest) then
            diags := diags.push { check := "evidence", code := s!"{what}-evidence-mismatch", decl := e.decl, message := s!"{what} entry {e.decl} and its evidence {e.evidence} disagree on the declaration or its fingerprints" }
  ({ check := "evidence", diags, data := [("dir", Json.str ((cfg.evidenceDir.map (·.toString)).getD "")),
      ("records", Json.arr rows)] }, fp)

end ReviewKit
