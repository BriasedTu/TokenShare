import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℕ)
  (h₀ : a 1 = 1)
  (h₁ : a 2 = 1)
  (h₂ : ∀ n, a (n + 2) = a (n + 1) + a n) : ∀ n, a (n + 6) = 8 * a (n + 1) + 5 * a n := by
  intro n
  have h2 := h₂ n
  have h3 := h₂ (n + 1)
  have h4 := h₂ (n + 2)
  have h5 := h₂ (n + 3)
  have h6 := h₂ (n + 4)
  norm_num [Nat.add_assoc] at h3 h4 h5 h6
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℕ)
  (h₀ : a 1 = 1)
  (h₁ : a 2 = 1)
  (h₂ : ∀ n, a (n + 2) = a (n + 1) + a n) (node_six_step_formula : ∀ n, a (n + 6) = 8 * a (n + 1) + 5 * a n) : ∀ n, a (n + 6) % 4 = a n % 4 := by
  intro n
  rw [node_six_step_formula]
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℕ)
  (h₀ : a 1 = 1)
  (h₁ : a 2 = 1)
  (h₂ : ∀ n, a (n + 2) = a (n + 1) + a n) (node_modular_period : ∀ n, a (n + 6) % 4 = a n % 4) : (a 100) % 4 = 3 := by
  have h3 := h₂ 1
  have h4 := h₂ 2
  norm_num at h3 h4
  have base : a 4 = 3 := by omega
  have reduce : ∀ k, a (4 + 6 * k) % 4 = a 4 % 4 := by
    intro k
    induction k with
    | zero => simp
    | succ k ih =>
      have hp := node_modular_period (4 + 6 * k)
      convert hp.trans ih using 1
  have h := reduce 16
  norm_num [base] at h
  exact h

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_483 (a : ℕ → ℕ)
  (h₀ : a 1 = 1)
  (h₁ : a 2 = 1)
  (h₂ : ∀ n, a (n + 2) = a (n + 1) + a n) : (a 100) % 4 = 3 := by
  have node_six_step_formula : ∀ n, a (n + 6) = 8 * a (n + 1) + 5 * a n := by
    intro n
    have h2 := h₂ n
    have h3 := h₂ (n + 1)
    have h4 := h₂ (n + 2)
    have h5 := h₂ (n + 3)
    have h6 := h₂ (n + 4)
    norm_num [Nat.add_assoc] at h3 h4 h5 h6
    omega
  have node_modular_period : ∀ n, a (n + 6) % 4 = a n % 4 := by
    intro n
    rw [node_six_step_formula]
    omega
  have node_root : (a 100) % 4 = 3 := by
    have h3 := h₂ 1
    have h4 := h₂ 2
    norm_num at h3 h4
    have base : a 4 = 3 := by omega
    have reduce : ∀ k, a (4 + 6 * k) % 4 = a 4 % 4 := by
      intro k
      induction k with
      | zero => simp
      | succ k ih =>
        have hp := node_modular_period (4 + 6 * k)
        convert hp.trans ih using 1
    have h := reduce 16
    norm_num [base] at h
    exact h
  exact node_root

#print axioms mathd_numbertheory_483
