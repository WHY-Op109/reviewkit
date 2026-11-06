/-
ReviewKit: diagnostics, module classification and dependency extraction shared by the checks.
-/
import Lean
import ReviewKit.Config
import ReviewKit.Attr

open Lean

namespace ReviewKit

/-! ## Diagnostics -/

inductive Severity where
  | error | warning | info
  deriving BEq, Inhabited, Repr

def Severity.toString : Severity → String
  | .error => "error" | .warning => "warning" | .info => "info"

/-- One finding of a check. `code` is a stable identifier that fault fixtures match on. -/
structure Diag where
  check : String
  code : String
  severity : Severity := .error
  decl : Option Name := none
  modName : Option Name := none
  message : String
  path : Array Name := #[]
  deriving Inhabited

def Diag.toJson (d : Diag) : Json := Json.mkObj <|
  [("check", Json.str d.check), ("code", Json.str d.code),
   ("severity", Json.str d.severity.toString), ("message", Json.str d.message)] ++
  (match d.decl with | some n => [("decl", Json.str n.toString)] | none => []) ++
  (match d.modName with | some m => [("module", Json.str m.toString)] | none => []) ++
  (if d.path.isEmpty then [] else [("path", Json.arr (d.path.map (Json.str ·.toString)))])

/-- A check's result: diagnostics plus check-specific data. -/
structure Report where
  check : String
  diags : Array Diag := #[]
  data : List (String × Json) := []
  deriving Inhabited

def Report.errors (r : Report) : Nat := (r.diags.filter (·.severity == .error)).size
def Report.ok (r : Report) : Bool := r.errors == 0

def Report.toJson (r : Report) : Json := Json.mkObj <|
  [("check", Json.str r.check), ("ok", Json.bool r.ok),
   ("errors", Lean.toJson r.errors),
   ("warnings", Lean.toJson (r.diags.filter (·.severity == .warning)).size),
   ("diagnostics", Json.arr (r.diags.map Diag.toJson))] ++ r.data

/-! ## Names and modules -/

def anyPrefix (ps : Array Name) (n : Name) : Bool := ps.any (·.isPrefixOf n)

def nameJson (n : Name) : Json := Json.str n.toString

def sortNames (xs : Array Name) : Array Name := xs.qsort (fun a b => a.toString < b.toString)

def moduleOf? (env : Environment) (c : Name) : Option Name :=
  (env.getModuleIdxFor? c).map fun i => env.header.moduleNames[i.toNat]!

/-- Constants declared in a module (empty if the module is not loaded). -/
def constsOfModule (env : Environment) (m : Name) : Array Name :=
  match env.header.moduleNames.idxOf? m with
  | some i => env.header.moduleData[i]!.constNames
  | none => #[]

/-- How the closure walk treats a module. -/
inductive ModClass where
  /-- not entered (Lean core, Mathlib, ...) -/
  | background
  /-- entered and listed with this tag -/
  | tagged (tag : String)
  /-- project code outside every closure class: a violation if reached -/
  | otherProject
  /-- matches nothing in the configuration: a violation if reached (fail-safe) -/
  | unclassified
  deriving BEq, Inhabited

def classify (cfg : Config) (m : Name) : ModClass :=
  match cfg.classes.find? (fun c => anyPrefix c.modules m) with
  | some c => .tagged c.tag
  | none =>
    if anyPrefix cfg.project m then .otherProject
    else if anyPrefix cfg.background m then .background
    else .unclassified

def isProjectModule (cfg : Config) (m : Name) : Bool := anyPrefix cfg.project m

def isProjectConst (cfg : Config) (env : Environment) (c : Name) : Bool :=
  match moduleOf? env c with
  | some m => isProjectModule cfg m
  | none => false

/-- All constants of the project's modules. -/
def projectConsts (cfg : Config) (env : Environment) : Array Name := Id.run do
  let mut out := #[]
  for i in [0:env.header.moduleNames.size] do
    if isProjectModule cfg env.header.moduleNames[i]! then
      out := out ++ env.header.moduleData[i]!.constNames
  out

/-! ## Dependencies -/

/-- Constants used by an expression, including the structure names of projections
(`Expr.getUsedConstants` omits those). Shared subterms are visited once: proof terms are DAGs
that are exponentially larger as trees. -/
def usedConsts (e : Expr) : Array Name := Id.run do
  let mut s : NameSet := e.foldConsts {} fun c s => s.insert c
  do
    let mut seen : Std.HashSet Expr := {}
    let mut stack : Array Expr := #[e]
    while !stack.isEmpty do
      let x := stack.back!
      stack := stack.pop
      if seen.contains x then continue
      seen := seen.insert x
      match x with
      | .proj tn _ b => s := s.insert tn; stack := stack.push b
      | .app f a => stack := stack.push f |>.push a
      | .lam _ t b _ | .forallE _ t b _ => stack := stack.push t |>.push b
      | .letE _ t v b _ => stack := stack.push t |>.push v |>.push b
      | .mdata _ b => stack := stack.push b
      | _ => pure ()
  return s.toArray

/-- Dependencies of a declaration. Statement level (`proofs = false`): its type, the value of a
definition, and the constructors of an inductive type (so a structure's field types are seen,
L3 report §1). With proofs: theorem proofs and **opaque values** too. An opaque value carries no
meaning (the kernel never unfolds it), so the statement-level walk and the fingerprints skip it;
but it can hide an admission (`opaque x : Nat := sorry` depends on `sorryAx`, as `#print axioms`
shows), so the axiom audit's trace, which uses `proofs = true`, follows it (D44, Codex's A1
review). -/
def depsOf (info : ConstantInfo) (proofs : Bool) : Array Name :=
  let t := usedConsts info.type
  let v := match info with
    | .thmInfo val => if proofs then usedConsts val.value else #[]
    | .opaqueInfo val => if proofs then usedConsts val.value else #[]
    | .defnInfo val => usedConsts val.value
    | _ => #[]
  let c := match info with
    | .inductInfo val => val.ctors.toArray
    | .ctorInfo val => #[val.induct]
    | _ => #[]
  t ++ v ++ c

/-- The members of a declaration's inductive block: all types of a (mutual) inductive
declaration and their constructors. Cycles in `depsOf` occur only inside such blocks
(inductive ↔ constructor), so treating a block as one node gives an order-independent fixed
point (D43/F21). Other declarations form singleton blocks. -/
def blockOf (env : Environment) (n : Name) : Array Name :=
  let indAll : Option (List Name) := match env.find? n with
    | some (.inductInfo v) => some v.all
    | some (.ctorInfo v) => match env.find? v.induct with
      | some (.inductInfo w) => some w.all
      | _ => none
    | _ => none
  match indAll with
  | none => #[n]
  | some all => Id.run do
    let mut out := #[]
    for i in all do
      out := out.push i
      if let some (.inductInfo v) := env.find? i then
        out := out ++ v.ctors.toArray
    out

/-- The block's representative: its first member. -/
def blockRep (env : Environment) (n : Name) : Name := (blockOf env n)[0]!

/-! ## Declaration filters -/

/-- Compiler-generated helpers are left out of the key-definition list. -/
def isUserFacing (env : Environment) (n : Name) : Bool :=
  !n.isInternalDetail && !isAuxRecursor env n && !isNoConfusion env n &&
    !(match env.find? n with | some (.recInfo _) => true | some (.ctorInfo _) => true | _ => false) &&
    !(match n with
      | .str _ s => ["ctorIdx", "noConfusionType", "inj", "injEq", "sizeOf_spec", "eq_1",
          "eq_def", "ext_iff", "ext"].contains s
      | _ => false)

/-- The nearest user-facing name: strips internal suffixes such as `_proof_1` or `match_1`, so a
`sorry` inside an auxiliary lemma is attributed to the declaration that owns it. -/
partial def ownerOf (env : Environment) (n : Name) : Name :=
  if n.isInternalDetail then
    match n with
    | .str p _ | .num p _ => if p.isAnonymous then n else ownerOf env p
    | .anonymous => n
  else n

/-- Whether a declaration uses `sorryAx` itself: in its type, a definition's or theorem's value,
or an opaque's value (an admission hidden in an opaque body is a sorry source, D44). -/
def directSorry (info : ConstantInfo) : Bool :=
  (usedConsts info.type).contains ``sorryAx ||
    (match info with
      | .thmInfo v => (usedConsts v.value).contains ``sorryAx
      | .defnInfo v => (usedConsts v.value).contains ``sorryAx
      | .opaqueInfo v => (usedConsts v.value).contains ``sorryAx
      | _ => false)

/-- Placeholder kind of one declaration, if any. -/
def placeholderKind? (env : Environment) (n : Name) : Option String :=
  if placeholderAttr.hasTag env n then some "@[placeholder]" else
  match env.find? n with
  | some (.opaqueInfo _) => some "opaque"
  | some (.defnInfo v) =>
      if (usedConsts v.value).contains ``sorryAx then some "sorry-valued def" else none
  | _ => none

def kindOf (info : ConstantInfo) : String :=
  match info with
  | .defnInfo _ => "def" | .thmInfo _ => "theorem" | .opaqueInfo _ => "opaque"
  | .axiomInfo _ => "axiom" | .quotInfo _ => "quot" | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor" | .recInfo _ => "recursor"

end ReviewKit
