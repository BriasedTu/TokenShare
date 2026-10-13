import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : ∃ x, x ^ 4 + a * x ^ 3 + b * x ^ 2 + a * x + 1 = 0) : ∃ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 ∧ 4 ≤ y ^ 2 := by
  obtain ⟨x, hx⟩ := h₀
  have hne : x ≠ 0 := by intro hz; norm_num [hz] at hx
  refine ⟨(x ^ 2 + 1) / x, ?_, ?_⟩
  · field_simp [hne]
    nlinarith
  · have hsq : 0 < x ^ 2 := sq_pos_of_ne_zero hne
    rw [div_pow]
    apply (le_div_iff₀ hsq).2
    nlinarith [sq_nonneg (x ^ 2 - 1)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : ∃ x, x ^ 4 + a * x ^ 3 + b * x ^ 2 + a * x + 1 = 0) : ∀ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 → (y ^ 2 - 2) ^ 2 ≤ (a ^ 2 + b ^ 2) * (y ^ 2 + 1) := by
  intro y hy
  have e : y ^ 2 - 2 = -(a * y + b) := by linarith
  rw [e]
  nlinarith [sq_nonneg (a - b * y)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : ∃ x, x ^ 4 + a * x ^ 3 + b * x ^ 2 + a * x + 1 = 0) (node_reciprocal_reduction : ∃ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 ∧ 4 ≤ y ^ 2) (node_coefficient_cauchy : ∀ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 → (y ^ 2 - 2) ^ 2 ≤ (a ^ 2 + b ^ 2) * (y ^ 2 + 1)) : 4 / 5 ≤ a ^ 2 + b ^ 2 := by
  obtain ⟨y, hy, hb⟩ := node_reciprocal_reduction
  have hc := node_coefficient_cauchy y hy
  have hp : 0 ≤ (y ^ 2 - 4) * (5 * y ^ 2 - 4) := mul_nonneg (by linarith) (by linarith)
  by_contra hn
  have hp' : 0 < (4 - 5 * (a ^ 2 + b ^ 2)) * (y ^ 2 + 1) := mul_pos (by linarith) (by positivity)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1973_p3 (a b : ℝ) (h₀ : ∃ x, x ^ 4 + a * x ^ 3 + b * x ^ 2 + a * x + 1 = 0) : 4 / 5 ≤ a ^ 2 + b ^ 2 := by
  have node_reciprocal_reduction : ∃ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 ∧ 4 ≤ y ^ 2 := by
    obtain ⟨x, hx⟩ := h₀
    have hne : x ≠ 0 := by intro hz; norm_num [hz] at hx
    refine ⟨(x ^ 2 + 1) / x, ?_, ?_⟩
    · field_simp [hne]
      nlinarith
    · have hsq : 0 < x ^ 2 := sq_pos_of_ne_zero hne
      rw [div_pow]
      apply (le_div_iff₀ hsq).2
      nlinarith [sq_nonneg (x ^ 2 - 1)]
  have node_coefficient_cauchy : ∀ y : ℝ, y ^ 2 + a * y + (b - 2) = 0 → (y ^ 2 - 2) ^ 2 ≤ (a ^ 2 + b ^ 2) * (y ^ 2 + 1) := by
    intro y hy
    have e : y ^ 2 - 2 = -(a * y + b) := by linarith
    rw [e]
    nlinarith [sq_nonneg (a - b * y)]
  have node_root : 4 / 5 ≤ a ^ 2 + b ^ 2 := by
    obtain ⟨y, hy, hb⟩ := node_reciprocal_reduction
    have hc := node_coefficient_cauchy y hy
    have hp : 0 ≤ (y ^ 2 - 4) * (5 * y ^ 2 - 4) := mul_nonneg (by linarith) (by linarith)
    by_contra hn
    have hp' : 0 < (4 - 5 * (a ^ 2 + b ^ 2)) * (y ^ 2 + 1) := mul_pos (by linarith) (by positivity)
    nlinarith
  exact node_root

#print axioms imo_1973_p3
