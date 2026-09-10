import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 0 = 1)
  (h₁ : ∀ n, a (n + 1) = (∏ k ∈ Finset.range (n + 1), a k) + 4) : ∀ n ≥ 1, a (n + 1) = (a n - 2) ^ 2 := by
  intro n hn
  obtain ⟨m, rfl⟩ := Nat.exists_eq_succ_of_ne_zero (by omega : n ≠ 0)
  have hp := h₁ m
  have hn := h₁ (m + 1)
  rw [Finset.prod_range_succ] at hn
  have hp' : (∏ k ∈ Finset.range (m + 1), a k) = a (m + 1) - 4 := by linarith
  change a (m + 1 + 1) = (a (m + 1) - 2) ^ 2
  rw [hn, hp']
  ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 0 = 1)
  (h₁ : ∀ n, a (n + 1) = (∏ k ∈ Finset.range (n + 1), a k) + 4) (node_quadratic_recurrence : ∀ n ≥ 1, a (n + 1) = (a n - 2) ^ 2) : ∀ n ≥ 1, 5 ≤ a n := by
  intro n hn
  induction n, hn using Nat.le_induction with
  | base =>
      have hb := h₁ 0
      norm_num [h₀] at hb
      linarith
  | succ n hn ih =>
      rw [node_quadratic_recurrence n hn]
      nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 0 = 1)
  (h₁ : ∀ n, a (n + 1) = (∏ k ∈ Finset.range (n + 1), a k) + 4) (node_quadratic_recurrence : ∀ n ≥ 1, a (n + 1) = (a n - 2) ^ 2) (node_uniform_lower_bound : ∀ n ≥ 1, 5 ≤ a n) : ∀ n ≥ 1, a n - Real.sqrt (a (n + 1)) = 2 := by
  intro n hn
  rw [node_quadratic_recurrence n hn, Real.sqrt_sq (by linarith [node_uniform_lower_bound n hn])]
  ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_aneqprodakp4_anmsqrtanp1eq2 (a : ℕ → ℝ) (h₀ : a 0 = 1)
  (h₁ : ∀ n, a (n + 1) = (∏ k ∈ Finset.range (n + 1), a k) + 4) : ∀ n ≥ 1, a n - Real.sqrt (a (n + 1)) = 2 := by
  have node_quadratic_recurrence : ∀ n ≥ 1, a (n + 1) = (a n - 2) ^ 2 := by
    intro n hn
    obtain ⟨m, rfl⟩ := Nat.exists_eq_succ_of_ne_zero (by omega : n ≠ 0)
    have hp := h₁ m
    have hn := h₁ (m + 1)
    rw [Finset.prod_range_succ] at hn
    have hp' : (∏ k ∈ Finset.range (m + 1), a k) = a (m + 1) - 4 := by linarith
    change a (m + 1 + 1) = (a (m + 1) - 2) ^ 2
    rw [hn, hp']
    ring
  have node_uniform_lower_bound : ∀ n ≥ 1, 5 ≤ a n := by
    intro n hn
    induction n, hn using Nat.le_induction with
    | base =>
        have hb := h₁ 0
        norm_num [h₀] at hb
        linarith
    | succ n hn ih =>
        rw [node_quadratic_recurrence n hn]
        nlinarith
  have node_root : ∀ n ≥ 1, a n - Real.sqrt (a (n + 1)) = 2 := by
    intro n hn
    rw [node_quadratic_recurrence n hn, Real.sqrt_sq (by linarith [node_uniform_lower_bound n hn])]
    ring
  exact node_root

#print axioms numbertheory_aneqprodakp4_anmsqrtanp1eq2
