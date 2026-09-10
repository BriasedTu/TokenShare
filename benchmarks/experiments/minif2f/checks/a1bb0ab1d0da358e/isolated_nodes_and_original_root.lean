import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : c < a + b)
  (h₂ : b < a + c)
  (h₃ : a < b + c) : 2 * (a ^ 2 * b * (a - b) + b ^ 2 * c * (b - c) + c ^ 2 * a * (c - a)) = (a + c - b) * (b + c - a) * (c - b) ^ 2 + (a + c - b) * (a + b - c) * (a - c) ^ 2 + (a + b - c) * (b + c - a) * (b - a) ^ 2 := by
  ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : c < a + b)
  (h₂ : b < a + c)
  (h₃ : a < b + c) (node_triangle_square_certificate : 2 * (a ^ 2 * b * (a - b) + b ^ 2 * c * (b - c) + c ^ 2 * a * (c - a)) = (a + c - b) * (b + c - a) * (c - b) ^ 2 + (a + c - b) * (a + b - c) * (a - c) ^ 2 + (a + b - c) * (b + c - a) * (b - a) ^ 2) : 0 ≤ a^2 * b * (a - b) + b^2 * c * (b - c) + c^2 * a * (c - a) := by
  have hx : 0 ≤ a + c - b := by linarith
  have hy : 0 ≤ a + b - c := by linarith
  have hz : 0 ≤ b + c - a := by linarith
  have hleft := mul_nonneg (mul_nonneg hx hz) (sq_nonneg (c - b))
  have hmiddle := mul_nonneg (mul_nonneg hx hy) (sq_nonneg (a - c))
  have hright := mul_nonneg (mul_nonneg hy hz) (sq_nonneg (b - a))
  nlinarith [node_triangle_square_certificate]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1983_p6 (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : c < a + b)
  (h₂ : b < a + c)
  (h₃ : a < b + c) : 0 ≤ a^2 * b * (a - b) + b^2 * c * (b - c) + c^2 * a * (c - a) := by
  have node_triangle_square_certificate : 2 * (a ^ 2 * b * (a - b) + b ^ 2 * c * (b - c) + c ^ 2 * a * (c - a)) = (a + c - b) * (b + c - a) * (c - b) ^ 2 + (a + c - b) * (a + b - c) * (a - c) ^ 2 + (a + b - c) * (b + c - a) * (b - a) ^ 2 := by
    ring
  have node_root : 0 ≤ a^2 * b * (a - b) + b^2 * c * (b - c) + c^2 * a * (c - a) := by
    have hx : 0 ≤ a + c - b := by linarith
    have hy : 0 ≤ a + b - c := by linarith
    have hz : 0 ≤ b + c - a := by linarith
    have hleft := mul_nonneg (mul_nonneg hx hz) (sq_nonneg (c - b))
    have hmiddle := mul_nonneg (mul_nonneg hx hy) (sq_nonneg (a - c))
    have hright := mul_nonneg (mul_nonneg hy hz) (sq_nonneg (b - a))
    nlinarith [node_triangle_square_certificate]
  exact node_root

#print axioms imo_1983_p6
