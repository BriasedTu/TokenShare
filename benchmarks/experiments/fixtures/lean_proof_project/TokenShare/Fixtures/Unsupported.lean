import TokenShare.Helper

namespace TokenShareFixtures

axiom opaque_predicate : Nat -> Prop

theorem unsupported_goal_shape (n : Nat) (h : opaque_predicate n) : opaque_predicate n := by
  exact h

end TokenShareFixtures
