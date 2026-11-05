/- ReviewKit toy tests: a thin Foundations layer (D10). -/
import ReviewKit.Attr
import RKUp
noncomputable section
namespace RKToy.Foundations
abbrev Obj := RKUp.UpObj
abbrev Thing := RKUp.Thing
opaque CatPt : NonemptyType.{0}
@[placeholder] def Cat : Type := CatPt.type
instance : Nonempty Cat := CatPt.property
end RKToy.Foundations
end
