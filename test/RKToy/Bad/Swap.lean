-- Planted fault (fingerprint / pin, D43/F03): a statement with one opaque object swapped for
-- another of the same type must not match the pin; an exact copy (the control) must.
import RKToy.Foundations
namespace RKToy.Bad.Swap
@[placeholder] opaque ObjA : Type
@[placeholder] opaque ObjB : Type
theorem mainA : Nonempty (ObjA → ObjA) := sorry
theorem mainB : Nonempty (ObjA → ObjB) := sorry
theorem mainCopy : Nonempty (ObjA → ObjA) := sorry
end RKToy.Bad.Swap
