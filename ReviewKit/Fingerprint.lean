/-
ReviewKit: fingerprints (K1 `fingerprint`, V7, D40 Finding 2, D43/F03, D43/F10).

A canonical serialisation of the kernel terms: de Bruijn indices, no binder names (binder kinds
kept), no metadata, universe parameters by position, a configurable list of notation constants
unfolded. Hashed with SHA-256 (`ReviewKit.Sha256`).

Two levels:
* **statement** (the hard pin, D43/F10): a declaration's own statement — its type; for a
  definition also its value; for an inductive type its whole block (types and constructors) —
  with every other constant referenced **by its qualified name only**. A change to the
  statement, or to *which* objects it names, changes it; a change inside a referenced definition
  does not.
* **digest** (transitive, Merkle): the same serialisation, but a constant from a content module
  (the project and reviewed providers such as Tau Ceti, `fingerprint.content`) is referenced by
  its qualified name **and** the digest of its own content — its body included, so a pin bump
  that changes a Tau Ceti definition's meaning changes the digest (D40 Finding 2). Background
  constants (Mathlib, core) are referenced by name plus a hash of their type.

Declaration identity is kept (D43/F03): references always carry the qualified name, so two
distinct opaque constants of the same type never hash alike. A declaration's *own* name is not
part of its own hash (so an exact copy under another name has the same statement fingerprint);
renames still trigger re-review, because reviews, budget entries and pins are keyed by name and
every referrer's hash changes.

Inductive blocks (types and constructors refer to each other) are hashed as one unit with
positional references inside the block, so the result never depends on traversal order.
-/
import Lean
import ReviewKit.Core
import ReviewKit.Sha256

open Lean

namespace ReviewKit

structure FpCtx where
  env : Environment
  cfg : Config
  /-- `false`: statement level (named references); `true`: transitive digest. -/
  deep : Bool
  /-- Members of the block being serialised: referenced by position. -/
  block : Array Name := #[]

structure FpState where
  blockHash : Std.HashMap Name String := {}
  shallow : Std.HashMap Name String := {}
  inProgress : NameSet := {}
  cycles : Array Name := #[]

abbrev FpM := ReaderT FpCtx (StateM FpState)

def serLevel (lps : List Name) : Level → String
  | .zero => "0"
  | .succ l => s!"(s {serLevel lps l})"
  | .max a b => s!"(max {serLevel lps a} {serLevel lps b})"
  | .imax a b => s!"(imax {serLevel lps a} {serLevel lps b})"
  | .param n => match lps.idxOf? n with
    | some i => s!"u{i}"
    | none => s!"?{n}"
  | .mvar _ => "?m"

def serLevels (lps : List Name) (ls : List Level) : String :=
  " ".intercalate (ls.map (serLevel lps))

def binderCode : BinderInfo → String
  | .default => "d" | .implicit => "i" | .strictImplicit => "s" | .instImplicit => "c"

def isContentConst (ctx : FpCtx) (n : Name) : Bool :=
  match moduleOf? ctx.env n with
  | some m => anyPrefix ctx.cfg.fpContent m
  | none => false

/-- A constant unfolded before hashing: a notation constant (both levels) or, in the digest, an
`abbrev` of an unfold module (Foundations re-exports, so "through Foundations" and "the provider
directly" hash alike, L3 report §3). -/
def unfoldable? (ctx : FpCtx) (n : Name) : Option ConstantInfo :=
  if ctx.cfg.fpNotations.contains n then ctx.env.find? n
  else if ctx.deep then
    match moduleOf? ctx.env n with
    | some m =>
      if anyPrefix ctx.cfg.fpUnfold m then
        match ctx.env.find? n with
        | some (info@(.defnInfo v)) => if v.hints.isAbbrev then some info else none
        | _ => none
      else none
    | none => none
  else none

def sha (s : String) : String := Sha256.ofString s

def quotCode : QuotKind → String
  | .type => "type" | .ctor => "ctor" | .lift => "lift" | .ind => "ind"

mutual
partial def ser (lps : List Name) (e : Expr) : FpM String := do
  let ctx ← read
  if let .const n ls := e.getAppFn then
    if let some info := unfoldable? ctx n then
      if info.levelParams.length == ls.length then
        return ← ser lps ((info.instantiateValueLevelParams! ls).beta e.getAppArgs)
  match e with
  | .bvar i => return s!"#{i}"
  | .sort l => return s!"(S {serLevel lps l})"
  | .const n ls => return s!"(C {← constRef n} {serLevels lps ls})"
  | .app f a => return s!"(A {← ser lps f} {← ser lps a})"
  | .lam _ t b bi => return s!"(L{binderCode bi} {← ser lps t} {← ser lps b})"
  | .forallE _ t b bi => return s!"(P{binderCode bi} {← ser lps t} {← ser lps b})"
  | .letE _ t v b _ => return s!"(Z {← ser lps t} {← ser lps v} {← ser lps b})"
  | .lit (.natVal k) => return s!"(N {k})"
  | .lit (.strVal s) => return s!"(T {s.quote})"
  | .mdata _ b => ser lps b
  | .proj tn i b => return s!"(J {← constRef tn} {i} {← ser lps b})"
  | .fvar _ => return "?fvar"
  | .mvar _ => return "?mvar"

partial def constRef (n : Name) : FpM String := do
  let ctx ← read
  if let some i := ctx.block.idxOf? n then return s!"^{i}"
  if !ctx.deep then return n.toString
  if isContentConst ctx n then
    return s!"{n}#{← digestOf n}"
  else
    return s!"{n}:{← shallowOf n}"

/-- Background constants: a hash of the statement-level serialisation of their type. -/
partial def shallowOf (n : Name) : FpM String := do
  if let some h := (← get).shallow[n]? then return h
  let ctx ← read
  let h ← match ctx.env.find? n with
    | some info =>
      let t ← withReader (fun c => { c with deep := false, block := #[] })
        (ser info.levelParams info.type)
      pure ((sha t).drop 7 |>.take 16).toString
    | none => pure "missing"
  modify fun s => { s with shallow := s.shallow.insert n h }
  return h

/-- Serialise one block member. -/
partial def serMember (n : Name) : FpM String := do
  let ctx ← read
  let some info := ctx.env.find? n | return s!"(missing {n})"
  let lps := info.levelParams
  let ty ← ser lps info.type
  match info with
  | .defnInfo v => return s!"D {lps.length} {ty} {← ser lps v.value}"
  | .thmInfo _ => return s!"T {lps.length} {ty}"
  | .opaqueInfo _ => return s!"O {lps.length} {ty}"
  | .axiomInfo _ => return s!"X {lps.length} {ty}"
  | .quotInfo v => return s!"Q {quotCode v.kind}"
  | .inductInfo v => return s!"I {lps.length} {v.numParams} {v.numIndices} {ty}"
  | .ctorInfo v => return s!"K {lps.length} {v.cidx} {v.numParams} {v.numFields} {ty}"
  | .recInfo _ => return s!"R {lps.length} {ty}"

/-- Serialise the block of `n` (all members, positional self-references). -/
partial def serBlock (n : Name) : FpM String := do
  let ctx ← read
  let members := blockOf ctx.env n
  withReader (fun c => { c with block := members }) do
    let mut parts := #[]
    for m in members do parts := parts.push (← serMember m)
    return "[" ++ " | ".intercalate parts.toList ++ "]"

/-- Transitive digest of a content constant: the hash of its block's digest-level
serialisation, plus its position in the block. -/
partial def digestOf (n : Name) : FpM String := do
  let ctx ← read
  let members := blockOf ctx.env n
  let rep := members[0]!
  let idx := (members.idxOf? n).getD 0
  let bh ← match (← get).blockHash[rep]? with
    | some h => pure h
    | none =>
      if (← get).inProgress.contains rep then
        modify fun s => { s with cycles := s.cycles.push rep }
        pure "CYCLE"
      else
        modify fun s => { s with inProgress := s.inProgress.insert rep }
        let text ← withReader (fun c => { c with deep := true }) (serBlock rep)
        let h := sha text
        modify fun s => { s with inProgress := s.inProgress.erase rep,
                                  blockHash := s.blockHash.insert rep h }
        pure h
  if members.size == 1 then return bh
  return sha s!"M {idx} {bh}"
end

/-- The statement-level canonical text of a declaration: for a theorem, opaque or axiom its
type; for a definition its type and value; for an inductive type or constructor its whole
block. -/
def statementText (n : Name) : FpM String := do
  let ctx ← read
  let some info := ctx.env.find? n | return s!"(missing {n})"
  withReader (fun c => { c with deep := false }) do
    match info with
    | .inductInfo _ | .ctorInfo _ => serBlock n
    | _ => withReader (fun c => { c with block := #[n] }) (serMember n)

/-- Fingerprints of one run share one memo (L3 report §3: per-root memos are quadratic). -/
structure Fingerprinter where
  env : Environment
  cfg : Config
  state : FpState := {}

def Fingerprinter.run (fp : Fingerprinter) (deep : Bool) (x : FpM α) : α × Fingerprinter :=
  let (a, s) := (x.run { env := fp.env, cfg := fp.cfg, deep }).run fp.state
  (a, { fp with state := s })

/-- Statement fingerprint (`sha256:…`). -/
def Fingerprinter.statement (fp : Fingerprinter) (n : Name) : String × Fingerprinter :=
  let (t, fp) := fp.run false (statementText n)
  (sha t, fp)

/-- Transitive digest (`sha256:…`). -/
def Fingerprinter.digest (fp : Fingerprinter) (n : Name) : String × Fingerprinter :=
  fp.run true (digestOf n)

def Fingerprinter.text (fp : Fingerprinter) (n : Name) : String × Fingerprinter :=
  fp.run false (statementText n)

/-- The digest-level canonical text of a declaration's block, the declaration's position in the
block and the block's size. The digest is `sha256(text)` for a block of one, else
`sha256("M " ++ position ++ " " ++ sha256(text))` (`digestOf`); `review evidence` recomputes it
from a stored record. -/
def Fingerprinter.digestText (fp : Fingerprinter) (n : Name) : (String × Nat × Nat) × Fingerprinter :=
  fp.run true (do
    let ctx ← read
    let members := blockOf ctx.env n
    let idx := (members.idxOf? n).getD 0
    let t ← serBlock members[0]!
    return (t, idx, members.size))

/-! ## Evidence ids (data format v0, `FORMATS.md`) -/

/-- The first 12 hex digits of a `sha256:` fingerprint. -/
def hex12 (fp : String) : String := ((fp.drop 7).take 12).toString

/-- The id of the fingerprint evidence record of a declaration at the given fingerprints. -/
def evidenceId (decl : Name) (statement digest : String) : String :=
  s!"fingerprint:{decl}@{hex12 statement}.{hex12 digest}"

/-- A declaration id as one path component: characters outside `[A-Za-z0-9._-]` become
`_xHHHH_` (the code point in at least four hex digits). -/
def declPathComponent (n : Name) : String := Id.run do
  let mut out := ""
  for c in n.toString.toList do
    if c.isAlphanum && c.toNat < 128 || c == '.' || c == '_' || c == '-' then
      out := out.push c
    else
      let h := String.ofList (Nat.toDigits 16 c.toNat)
      out := out ++ "_x" ++ String.ofList (List.replicate (4 - min 4 h.length) '0') ++ h ++ "_"
  out

/-- Where a fingerprint record is stored, relative to the evidence directory. -/
def evidenceRelPath (decl : Name) (statement digest : String) : System.FilePath :=
  "fingerprint" / declPathComponent decl / s!"{hex12 statement}.{hex12 digest}.json"

end ReviewKit
