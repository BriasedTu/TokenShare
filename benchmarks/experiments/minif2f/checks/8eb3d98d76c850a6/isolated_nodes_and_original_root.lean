import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (t : ℕ → ℚ) (h₀ : t 1 = 20) (h₁ : t 2 = 21)
  (h₂ : ∀ n ≥ 3, t n = (5 * t (n - 1) + 1) / (25 * t (n - 2))) : t 3 = 53 / 250 ∧ t 4 = 103 / 26250 ∧ t 5 = 101 / 525 ∧ t 6 = 20 ∧ t 7 = 21 := by
  have h3 := h₂ 3 (by norm_num)
  norm_num [h₀, h₁] at h3
  have h4 := h₂ 4 (by norm_num)
  norm_num [h₁, h3] at h4
  have h5 := h₂ 5 (by norm_num)
  norm_num [h3, h4] at h5
  have h6 := h₂ 6 (by norm_num)
  norm_num [h4, h5] at h6
  have h7 := h₂ 7 (by norm_num)
  norm_num [h5, h6] at h7
  exact ⟨h3, h4, h5, h6, h7⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (t : ℕ → ℚ) (h₀ : t 1 = 20) (h₁ : t 2 = 21)
  (h₂ : ∀ n ≥ 3, t n = (5 * t (n - 1) + 1) / (25 * t (n - 2))) (node_initial_five_cycle : t 3 = 53 / 250 ∧ t 4 = 103 / 26250 ∧ t 5 = 101 / 525 ∧ t 6 = 20 ∧ t 7 = 21) : ∀ n : ℕ, t (n + 6) = t (n + 1) ∧ t (n + 7) = t (n + 2) := by
  intro n
  induction n with
  | zero => simpa [h₀, h₁] using node_initial_five_cycle.2.2.2
  | succ n ih =>
      constructor
      · simpa [Nat.succ_eq_add_one, Nat.add_assoc] using ih.2
      · change t (n + 8) = t (n + 3)
        have hh := h₂ (n + 8) (by omega)
        have hl := h₂ (n + 3) (by omega)
        simp only [show n + 8 - 1 = n + 7 by omega, show n + 8 - 2 = n + 6 by omega] at hh
        simp only [show n + 3 - 1 = n + 2 by omega, show n + 3 - 2 = n + 1 by omega] at hl
        rw [ih.2, ih.1] at hh
        exact hh.trans hl.symm

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (t : ℕ → ℚ) (h₀ : t 1 = 20) (h₁ : t 2 = 21)
  (h₂ : ∀ n ≥ 3, t n = (5 * t (n - 1) + 1) / (25 * t (n - 2))) (node_initial_five_cycle : t 3 = 53 / 250 ∧ t 4 = 103 / 26250 ∧ t 5 = 101 / 525 ∧ t 6 = 20 ∧ t 7 = 21) (node_period_five : ∀ n : ℕ, t (n + 6) = t (n + 1) ∧ t (n + 7) = t (n + 2)) : ↑(t 2020).den + (t 2020).num = 626 := by
  have hm : ∀ m : ℕ, t (5 * m + 5) = t 5 := by
    intro m
    induction m with
    | zero => rfl
    | succ m ih =>
        have hp := (node_period_five (5 * m + 4)).1
        have he : 5 * (m + 1) + 5 = 5 * m + 4 + 6 := by omega
        rw [he, hp]
        simpa [Nat.add_assoc] using ih
  have hv : t 2020 = 101 / 525 := (hm 403).trans node_initial_five_cycle.2.2.1
  rw [hv]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aimeII_2020_p6 (t : ℕ → ℚ) (h₀ : t 1 = 20) (h₁ : t 2 = 21)
  (h₂ : ∀ n ≥ 3, t n = (5 * t (n - 1) + 1) / (25 * t (n - 2))) : ↑(t 2020).den + (t 2020).num = 626 := by
  have node_initial_five_cycle : t 3 = 53 / 250 ∧ t 4 = 103 / 26250 ∧ t 5 = 101 / 525 ∧ t 6 = 20 ∧ t 7 = 21 := by
    have h3 := h₂ 3 (by norm_num)
    norm_num [h₀, h₁] at h3
    have h4 := h₂ 4 (by norm_num)
    norm_num [h₁, h3] at h4
    have h5 := h₂ 5 (by norm_num)
    norm_num [h3, h4] at h5
    have h6 := h₂ 6 (by norm_num)
    norm_num [h4, h5] at h6
    have h7 := h₂ 7 (by norm_num)
    norm_num [h5, h6] at h7
    exact ⟨h3, h4, h5, h6, h7⟩
  have node_period_five : ∀ n : ℕ, t (n + 6) = t (n + 1) ∧ t (n + 7) = t (n + 2) := by
    intro n
    induction n with
    | zero => simpa [h₀, h₁] using node_initial_five_cycle.2.2.2
    | succ n ih =>
        constructor
        · simpa [Nat.succ_eq_add_one, Nat.add_assoc] using ih.2
        · change t (n + 8) = t (n + 3)
          have hh := h₂ (n + 8) (by omega)
          have hl := h₂ (n + 3) (by omega)
          simp only [show n + 8 - 1 = n + 7 by omega, show n + 8 - 2 = n + 6 by omega] at hh
          simp only [show n + 3 - 1 = n + 2 by omega, show n + 3 - 2 = n + 1 by omega] at hl
          rw [ih.2, ih.1] at hh
          exact hh.trans hl.symm
  have node_root : ↑(t 2020).den + (t 2020).num = 626 := by
    have hm : ∀ m : ℕ, t (5 * m + 5) = t 5 := by
      intro m
      induction m with
      | zero => rfl
      | succ m ih =>
          have hp := (node_period_five (5 * m + 4)).1
          have he : 5 * (m + 1) + 5 = 5 * m + 4 + 6 := by omega
          rw [he, hp]
          simpa [Nat.add_assoc] using ih
    have hv : t 2020 = 101 / 525 := (hm 403).trans node_initial_five_cycle.2.2.1
    rw [hv]
    norm_num
  exact node_root

#print axioms aimeII_2020_p6
