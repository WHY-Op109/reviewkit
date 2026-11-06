-- Planted fault (imports, warning only): an abstract that names upstream objects directly
-- instead of through Foundations names.
import RKToy.Foundations
namespace RKToy.Bad.AbstractDirect
def UsesUp : Type := RKUp.UpObj
def UsesThing : Type := RKUp.Thing
end RKToy.Bad.AbstractDirect
