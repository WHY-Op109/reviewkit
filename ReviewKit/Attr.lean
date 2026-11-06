/-
ReviewKit: the `@[placeholder]` attribute.
-/
import Lean

open Lean

/-- `@[placeholder]` marks a deliberate, data-only placeholder declaration: an
`opaque` constant or a definition over one, standing for an object that does not exist yet.
The `review placeholders` census reads this tag; it is the only way a structure placeholder
is seen. -/
initialize placeholderAttr : TagAttribute ←
  registerTagAttribute `placeholder "marks a deliberate, data-only placeholder (ReviewKit)"
