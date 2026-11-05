-- Planted fault (axioms, D27 test 1): a property axiom, its use, and a stray sorry.
import RKToy.Abstract
namespace RKToy.Bad.PropAxiom
axiom bad_prop : ∀ n : Nat, RKToy.Abstract.IsGood n
theorem uses_axiom : RKToy.Abstract.IsGood 5 := bad_prop 5
theorem stray : 2 + 2 = 4 := sorry
end RKToy.Bad.PropAxiom
