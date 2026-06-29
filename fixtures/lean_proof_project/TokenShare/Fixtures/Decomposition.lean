import TokenShare.Helper

namespace TokenShareFixtures

theorem and_swap (p q : Prop) : p ∧ q -> q ∧ p := by
  intro h
  exact And.intro h.right h.left

end TokenShareFixtures
