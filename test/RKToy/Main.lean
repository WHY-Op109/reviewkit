import RKToy.Abstract
import RKToy.Impl
namespace RKToy.Main
theorem isGood_helper : RKToy.Abstract.IsGood RKToy.Impl.helperBound := by
  intro m hm
  exact ⟨RKToy.Impl.helperBound - m, by omega⟩
end RKToy.Main
