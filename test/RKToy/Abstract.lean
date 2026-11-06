/- ReviewKit toy tests: a formal abstract, importing only Foundations. -/
import RKToy.Foundations
noncomputable section
namespace RKToy.Abstract
open RKToy.Foundations
opaque DmodPt : NonemptyType.{0}
@[placeholder] def Dmod : Type := DmodPt.type
instance : Nonempty Dmod := DmodPt.property
/-- A structure placeholder: only the attribute finds it. -/
@[placeholder] structure Curve where
  carrier : Thing
  genus : Nat
def IsGood (n : Nat) : Prop := ∀ m, m ≤ n → ∃ k, m + k = n
theorem exists_bound (n : Nat) : ∃ b, n < b := sorry
def bound (n : Nat) : Nat := Classical.choose (exists_bound n)
@[placeholder] opaque Lfun : Dmod → Cat
def Statement : Prop := ∀ X : Curve, IsGood (bound X.genus)
theorem main : Statement := sorry
theorem isGood_zero : IsGood 0 := fun m hm => ⟨0, by omega⟩
end RKToy.Abstract
end
