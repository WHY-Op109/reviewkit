/- ReviewKit toy tests: fingerprint variants (V7, D43/F03). -/
import RKToy.Foundations
noncomputable section
namespace RKToy.Fp
def helper (n : Nat) : Nat := n + 1
def helperRenamed (n : Nat) : Nat := n + 1
def helperChanged (n : Nat) : Nat := n + 2
namespace V0
/-- Base doc comment. -/
def P (f : Nat → Nat) : Prop := ∀ x, 0 < x → ∃ y, f x < y ∧ helper x ≤ y
end V0
namespace V1
def P (g : Nat → Nat) : Prop := ∀ a, 0 < a → ∃ b, g a < b ∧ helper a ≤ b
end V1
namespace V2
/-- A completely different doc comment. -/
def P (f : Nat → Nat) : Prop :=
  ∀ x,
    0 < x →
      ∃ y, f x < y ∧ helper x ≤ y
end V2
namespace V3
def P (f : Nat → Nat) : Prop := ∃ y, ∀ x, 0 < x → f x < y ∧ helper x ≤ y
end V3
namespace V4
def P (f : Nat → Nat) : Prop := ∀ x, ∃ y, f x < y ∧ helper x ≤ y
end V4
namespace V5
def P (f : Nat → Nat) : Prop := ∀ x, 0 < x → ∃ y, f x < y ∧ helper x < y
end V5
namespace V6
def P (f : Nat → Nat) : Prop := ∀ x, 0 < x → ∃ y, f x < y ∧ helperRenamed x ≤ y
end V6
namespace V7
def P (f : Nat → Nat) : Prop := ∀ x, 0 < x → ∃ y, f x < y ∧ helperChanged x ≤ y
end V7
namespace V8
def P (f : Nat → Nat) : Prop := ∀ x, x > 0 → ∃ y, f x < y ∧ helper x ≤ y
end V8
namespace V9
def P {f : Nat → Nat} : Prop := ∀ x, 0 < x → ∃ y, f x < y ∧ helper x ≤ y
end V9
universe u v
namespace U0
def R (α : Type u) : Prop := Nonempty α
end U0
namespace U1
def R (α : Type v) : Prop := Nonempty α
end U1
namespace F0
def S : Type := RKToy.Foundations.Obj
end F0
namespace F1
def S : Type := RKUp.UpObj
end F1
namespace T0
theorem t (n : Nat) : ∃ b, n < b := ⟨n + 1, Nat.lt_succ_self n⟩
def d (n : Nat) : Nat := Classical.choose (t n)
end T0
/-- Distinct opaque objects of the same type (D43/F03, Astra's probe A). -/
@[placeholder] opaque ObjA : Type
@[placeholder] opaque ObjB : Type
def SwapAA : Type := ObjA → ObjA
def SwapAB : Type := ObjA → ObjB
end RKToy.Fp
end
