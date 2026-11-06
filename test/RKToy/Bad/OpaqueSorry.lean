/- ReviewKit toy fault (D44): an admission hidden in an opaque body. `#print axioms` shows
`sorryAx`; the trace and the direct-sorry scan must see it. -/
import RKToy.Abstract
noncomputable section
namespace RKToy.Bad.OpaqueSorry
@[placeholder] opaque hidden : Nat := sorry
theorem usesHidden : hidden = hidden := rfl
end RKToy.Bad.OpaqueSorry
end
