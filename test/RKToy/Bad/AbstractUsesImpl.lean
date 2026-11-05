-- Planted fault (closure): an abstract definition depends on implementation code.
import RKToy.Foundations
import RKToy.Impl
namespace RKToy.Bad.AbstractUsesImpl
-- allowed: a clean statement whose proof uses Impl
theorem okProof : ∃ n : Nat, 0 < n := ⟨_, RKToy.Impl.helper_pos⟩
-- the fault: a definition body uses Impl
def Bad : Prop := RKToy.Impl.helperBound = 42
end RKToy.Bad.AbstractUsesImpl
