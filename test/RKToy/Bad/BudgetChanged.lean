-- Planted fault (axioms): a budgeted theorem whose statement became `False`. The budget
-- entry is keyed by name and bound to the statement fingerprint, so it must fail.
import RKToy.Abstract
namespace RKToy.Bad.BudgetChanged
theorem exists_bound (_n : Nat) : False := sorry
end RKToy.Bad.BudgetChanged
