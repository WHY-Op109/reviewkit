/-
ReviewKit: the project configuration (data format v0), read from a TOML file whose path the
caller passes (`--config`). The kit itself names no project: every module prefix, tag, rule and
file path comes from this file.
-/
import Lean
import Lake.Toml

open Lean Lake Lake.Toml

namespace ReviewKit

/-- A closure class: constants in these modules are entered by the closure walk and listed with
this tag (for example `abstract`, `foundations`, `upstream`). -/
structure ClosureClass where
  tag : String
  modules : Array Name
  deriving Inhabited, Repr

/-- An import rule: modules with prefix `module` may import only modules with a prefix in
`allow`. The first rule whose prefix matches a module applies. -/
structure ImportRule where
  module : Name
  allow : Array Name
  deriving Inhabited, Repr

/-- A stand-in, used only by fault fixtures: the declaration `decl` is checked as if it were
`target` when the sorry budget and the statement pin are looked up. -/
structure StandIn where
  decl : Name
  target : Name
  deriving Inhabited, Repr

structure Config where
  /-- The config file, for messages. -/
  path : System.FilePath := "."
  /-- Modules to import (the project roots). -/
  imports : Array Name := #[]
  /-- Module prefixes that are project code. -/
  project : Array Name := #[]
  /-- The formal abstract module. -/
  abstract : Name := .anonymous
  /-- Module prefixes not entered by the walk (trusted background: Lean core, Mathlib, ...). -/
  background : Array Name := #[]
  /-- Closure classes, in order (first match wins). -/
  classes : Array ClosureClass := #[]
  /-- The tag of the class whose constants are the closure roots' own module. -/
  allowedAxioms : Array Name := #[``propext, ``Classical.choice, ``Quot.sound]
  /-- The human-owned sorry budget (TOML), relative to the working directory. -/
  budgetFile : Option System.FilePath := none
  /-- Import rules. -/
  importRules : Array ImportRule := #[]
  /-- Foundations-name warning: module prefixes of the Foundations layer. -/
  fnFoundations : Array Name := #[]
  /-- Library prefixes whose direct use by the abstract is checked (for example `Mathlib`). -/
  fnChecked : Array Name := #[]
  /-- Basic vocabulary: name prefixes the abstract may use directly. -/
  fnBasic : Array Name := #[]
  /-- Substantial objects (exact names) that must reach the abstract through a Foundations name. -/
  fnRequired : Array Name := #[]
  /-- Module prefixes whose constants are fingerprinted by content (Merkle). -/
  fpContent : Array Name := #[]
  /-- Module prefixes whose `abbrev`s are unfolded in the transitive digest. -/
  fpUnfold : Array Name := #[]
  /-- Background notation constants unfolded before hashing. -/
  fpNotations : Array Name := #[``GT.gt, ``GE.ge]
  /-- The statement-pin file (TOML), relative to the working directory. -/
  pinFile : Option System.FilePath := none
  /-- Declarations whose own statements are hard-pinned. -/
  pinDecls : Array Name := #[]
  /-- Module prefixes whose placeholders must be tagged `@[placeholder]`. -/
  placeholderModules : Array Name := #[]
  /-- Machine evidence records (`evidence.dir`), relative to the working directory. -/
  evidenceDir : Option System.FilePath := none
  /-- Fault fixtures only. -/
  standIns : Array StandIn := #[]
  /-- Fault fixtures only: compute per-declaration traces with the roots in reverse order. -/
  reverseRoots : Bool := false
  deriving Inhabited

/-! ## TOML access with error collection -/

abbrev CfgM := StateT (Array String) Id

def cfgErr (msg : String) : CfgM Unit := modify (·.push msg)

def strOf? : Value → Option String
  | .string _ s => some s
  | _ => none

def getStr (t : Table) (k : Name) (ctx : String) : CfgM (Option String) := do
  match t.find? k with
  | none => return none
  | some (.string _ s) => return some s
  | some _ => cfgErr s!"{ctx}.{k}: expected a string"; return none

def getBool (t : Table) (k : Name) (ctx : String) : CfgM (Option Bool) := do
  match t.find? k with
  | none => return none
  | some (.boolean _ b) => return some b
  | some _ => cfgErr s!"{ctx}.{k}: expected a boolean"; return none

def getStrs (t : Table) (k : Name) (ctx : String) : CfgM (Array String) := do
  match t.find? k with
  | none => return #[]
  | some (.array _ xs) =>
    let mut out := #[]
    for x in xs do
      match x with
      | .string _ s => out := out.push s
      | _ => cfgErr s!"{ctx}.{k}: expected an array of strings"
    return out
  | some _ => cfgErr s!"{ctx}.{k}: expected an array of strings"; return #[]

def getNames (t : Table) (k : Name) (ctx : String) : CfgM (Array Name) := do
  return (← getStrs t k ctx).map String.toName

def getTable (t : Table) (k : Name) (ctx : String) : CfgM Table := do
  match t.find? k with
  | none => return {}
  | some (.table' _ x) => return x
  | some _ => cfgErr s!"{ctx}.{k}: expected a table"; return {}

def getTables (t : Table) (k : Name) (ctx : String) : CfgM (Array Table) := do
  match t.find? k with
  | none => return #[]
  | some (.array _ xs) =>
    let mut out := #[]
    for x in xs do
      match x with
      | .table' _ y => out := out.push y
      | _ => cfgErr s!"{ctx}.{k}: expected an array of tables"
    return out
  | some _ => cfgErr s!"{ctx}.{k}: expected an array of tables"; return #[]

def checkKeys (t : Table) (known : List String) (ctx : String) : CfgM Unit := do
  for k in t.keys do
    unless known.contains k.toString do
      cfgErr s!"{ctx}: unknown key '{k}' (known: {known})"

/-- Parse a TOML file into a table, or an error string. -/
def loadTomlFile (path : System.FilePath) : IO (Except String Table) := do
  unless ← path.pathExists do return .error s!"{path}: file not found"
  let input ← IO.FS.readFile path
  let ictx := Lean.Parser.mkInputContext input path.toString
  match ← (loadToml ictx).toBaseIO with
  | .ok t => return .ok t
  | .error log =>
    let msgs ← log.toList.mapM fun m => do
      return s!"{m.pos.line}:{m.pos.column}: {← m.data.toString}"
    return .error s!"{path}: {String.intercalate "; " msgs}"

/-- Top-level tables read by other tools (a papers manifest; `[extras]`, read by
`scripts/rk_extras.py`); accepted here without validation. -/
def reservedTables : List String := ["papers", "extras"]

def decodeConfig (path : System.FilePath) (t : Table) : CfgM Config := do
  checkKeys t (["format", "project", "closure", "axioms", "imports", "foundations_names",
    "fingerprint", "pin", "placeholders", "evidence"] ++ reservedTables) "config"
  match ← getStr t `format "config" with
  | some "reviewkit-config/0" => pure ()
  | some f => cfgErr s!"config.format: unsupported format '{f}' (expected reviewkit-config/0)"
  | none => cfgErr "config.format: missing (expected \"reviewkit-config/0\")"
  let p ← getTable t `project "config"
  checkKeys p ["import", "modules", "abstract"] "project"
  let c ← getTable t `closure "config"
  checkKeys c ["background", "class"] "closure"
  let mut classes := #[]
  for ct in ← getTables c `class "closure" do
    checkKeys ct ["tag", "modules"] "closure.class"
    let tag := (← getStr ct `tag "closure.class").getD ""
    if tag.isEmpty then cfgErr "closure.class: missing tag"
    classes := classes.push { tag, modules := ← getNames ct `modules "closure.class" }
  let a ← getTable t `axioms "config"
  checkKeys a ["allowed", "budget"] "axioms"
  let i ← getTable t `imports "config"
  checkKeys i ["rule"] "imports"
  let mut rules := #[]
  for rt in ← getTables i `rule "imports" do
    checkKeys rt ["module", "allow"] "imports.rule"
    rules := rules.push { module := ((← getStr rt `module "imports.rule").getD "").toName,
                          allow := ← getNames rt `allow "imports.rule" }
  let fn ← getTable t `foundations_names "config"
  checkKeys fn ["foundations", "checked", "basic", "required"] "foundations_names"
  let fp ← getTable t `fingerprint "config"
  checkKeys fp ["content", "unfold_abbrevs", "notations"] "fingerprint"
  let pin ← getTable t `pin "config"
  checkKeys pin ["file", "decls"] "pin"
  let ph ← getTable t `placeholders "config"
  checkKeys ph ["modules"] "placeholders"
  let ev ← getTable t `evidence "config"
  checkKeys ev ["dir"] "evidence"
  let allowed ← getNames a `allowed "axioms"
  let notations ← getNames fp `notations "fingerprint"
  return {
    path
    imports := ← getNames p `import "project"
    project := ← getNames p `modules "project"
    abstract := ((← getStr p `abstract "project").getD "").toName
    background := ← getNames c `background "closure"
    classes
    allowedAxioms := if allowed.isEmpty then #[``propext, ``Classical.choice, ``Quot.sound] else allowed
    budgetFile := (← getStr a `budget "axioms").map (·)
    importRules := rules
    fnFoundations := ← getNames fn `foundations "foundations_names"
    fnChecked := ← getNames fn `checked "foundations_names"
    fnBasic := ← getNames fn `basic "foundations_names"
    fnRequired := ← getNames fn `required "foundations_names"
    fpContent := ← getNames fp `content "fingerprint"
    fpUnfold := ← getNames fp `unfold_abbrevs "fingerprint"
    fpNotations := if notations.isEmpty then #[``GT.gt, ``GE.ge] else notations
    pinFile := (← getStr pin `file "pin").map (·)
    pinDecls := ← getNames pin `decls "pin"
    placeholderModules := ← getNames ph `modules "placeholders"
    evidenceDir := (← getStr ev `dir "evidence").map (·)
  }

def loadConfig (path : System.FilePath) : IO (Except String Config) := do
  match ← loadTomlFile path with
  | .error e => return .error e
  | .ok t =>
    let (cfg, errs) := (decodeConfig path t).run #[]
    if errs.isEmpty then
      if cfg.imports.isEmpty then return .error s!"{path}: project.import is empty"
      if cfg.abstract.isAnonymous then return .error s!"{path}: project.abstract is missing"
      return .ok cfg
    else return .error s!"{path}: {String.intercalate "; " errs.toList}"

end ReviewKit
