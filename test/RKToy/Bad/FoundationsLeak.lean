-- Planted fault (closure): a Foundations-class structure whose field type leaks Impl.
import RKToy.Foundations
import RKToy.Impl
namespace RKToy.Bad.FoundationsLeak
structure Wrapper where
  val : Nat
  pos : 0 < val ∧ val ≤ RKToy.Impl.helperBound
end RKToy.Bad.FoundationsLeak
