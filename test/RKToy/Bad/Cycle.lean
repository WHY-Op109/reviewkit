-- Planted fault (axioms): a sorry inside a constructor type. The inductive and its
-- constructor refer to each other; every root order must trace the sorry.
import RKToy.Abstract
namespace RKToy.Bad.Cycle
inductive T where
  | mk (n : Nat) (h : n = (sorry : Nat)) : T
def aUsesMk : Nat → Prop := fun n => ∃ h, T.mk n h = T.mk n h
def useT : T → Nat := fun _ => 0
end RKToy.Bad.Cycle
