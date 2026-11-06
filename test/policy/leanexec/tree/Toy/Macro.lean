macro "twice" t:tactic : tactic => `(tactic| ($t; $t))
