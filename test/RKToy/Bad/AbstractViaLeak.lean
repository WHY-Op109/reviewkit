-- Planted fault (closure): the abstract uses only the leaky Foundations structure; caught only
-- because constructors are walked (L3 report §1).
import RKToy.Bad.FoundationsLeak
namespace RKToy.Bad.AbstractViaLeak
def UsesWrapper : Prop := ∀ w : RKToy.Bad.FoundationsLeak.Wrapper, 0 < w.val
end RKToy.Bad.AbstractViaLeak
