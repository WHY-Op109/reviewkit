/- ReviewKit toy CONTROL for `OpaqueSorry`: the same shape with an honest witness. -/
import RKToy.Abstract
noncomputable section
namespace RKToy.Bad.OpaqueWitness
@[placeholder] opaque honest : Nat
theorem usesHonest : honest = honest := rfl
end RKToy.Bad.OpaqueWitness
end
