-- A toy lakefile.lean beside lakefile.toml: Lake reads this one, so the checked lakefile.toml
-- would not be the configuration in force (pin-lakefile-shadowed).
import Lake
open Lake DSL
package Toy
require UpstreamLib from git "https://github.com/attacker/upstreamlib" @ "main"
