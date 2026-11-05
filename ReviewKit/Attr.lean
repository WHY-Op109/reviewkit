/-
ReviewKit: the `@[placeholder]` attribute (K2).
-/
import Lean

open Lean

/-- `@[placeholder]` marks a deliberate, data-only placeholder declaration (D13, D15): an
`opaque` constant or a definition over one, standing for an object that does not exist yet.
The `review placeholders` census reads this tag; it is the only way a structure placeholder
is seen. -/
initialize placeholderAttr : TagAttribute ←
  registerTagAttribute `placeholder "marks a deliberate, data-only placeholder (ReviewKit)"
