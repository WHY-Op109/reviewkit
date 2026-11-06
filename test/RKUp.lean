/- ReviewKit toy tests: a reviewed upstream provider library
(walked by the closure, fingerprinted by content). -/
namespace RKUp
def UpObj : Type := Nat
structure Thing where
  n : Nat
  pos : 0 < n
def V1.f (n : Nat) : Nat := n + 1
def V2.f (n : Nat) : Nat := n + 2
end RKUp
