/- A plain definition: no code runs at build time. -/
-- a comment mentioning #eval and initialize is skipped
@[simp, elab_as_elim] theorem t : True := trivial
def double (n : Nat) : Nat := 2 * n
