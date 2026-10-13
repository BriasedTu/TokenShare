import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ)
  (h₀ : ∀ n, 1000 ≤ n → f n = n - 3)
  (h₁ : ∀ n, n < 1000 → f n = f (f (n + 5))) : f 997 = 998 ∧ f 998 = 997 := by
  have ha := h₁ 999 (by omega)
  norm_num [h₀ 1004 (by omega), h₀ 1001 (by omega)] at ha
  have hb := h₁ 998 (by omega)
  norm_num [h₀ 1003 (by omega), h₀ 1000 (by omega)] at hb
  have hc := h₁ 997 (by omega)
  norm_num [h₀ 1002 (by omega), ha] at hc
  exact ⟨hc, hb⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ)
  (h₀ : ∀ n, 1000 ≤ n → f n = n - 3)
  (h₁ : ∀ n, n < 1000 → f n = f (f (n + 5))) (node_closed_two_cycle : f 997 = 998 ∧ f 998 = 997) : ∀ k : ℕ, f (999 - (k : ℤ)) = if Even k then 998 else 997 := by
  have h999 := h₁ 999 (by omega)
  norm_num [h₀ 1004 (by omega), h₀ 1001 (by omega)] at h999
  have h996 := h₁ 996 (by omega)
  norm_num [h₀ 1001 (by omega), node_closed_two_cycle.2] at h996
  have h995 := h₁ 995 (by omega)
  norm_num [h₀ 1000 (by omega), node_closed_two_cycle.1] at h995
  intro k
  induction k using Nat.strong_induction_on with
  | h k ih =>
    by_cases hk : k < 5
    · interval_cases k <;> norm_num [h999, h996, h995, node_closed_two_cycle.1, node_closed_two_cycle.2]
    · have hk5 : 5 ≤ k := by omega
      have hi := ih (k - 5) (by omega)
      have hidx : (999 : ℤ) - (k : ℤ) + 5 = 999 - ((k - 5 : ℕ) : ℤ) := by omega
      rw [h₁ (999 - (k : ℤ)) (by omega), hidx, hi]
      by_cases he : Even k
      · have hkmod := Nat.even_iff.mp he
        have ho : ¬ Even (k - 5) := Nat.not_even_iff.mpr (by omega)
        simp [he, ho, node_closed_two_cycle.1]
      · have hkmod := Nat.not_even_iff.mp he
        have ho : Even (k - 5) := Nat.even_iff.mpr (by omega)
        simp [he, ho, node_closed_two_cycle.2]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ)
  (h₀ : ∀ n, 1000 ≤ n → f n = n - 3)
  (h₁ : ∀ n, n < 1000 → f n = f (f (n + 5))) (node_below_threshold_formula : ∀ k : ℕ, f (999 - (k : ℤ)) = if Even k then 998 else 997) : f 84 = 997 := by
  have h := node_below_threshold_formula 915
  norm_num at h
  exact h

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1984_p7 (f : ℤ → ℤ)
  (h₀ : ∀ n, 1000 ≤ n → f n = n - 3)
  (h₁ : ∀ n, n < 1000 → f n = f (f (n + 5))) : f 84 = 997 := by
  have node_closed_two_cycle : f 997 = 998 ∧ f 998 = 997 := by
    have ha := h₁ 999 (by omega)
    norm_num [h₀ 1004 (by omega), h₀ 1001 (by omega)] at ha
    have hb := h₁ 998 (by omega)
    norm_num [h₀ 1003 (by omega), h₀ 1000 (by omega)] at hb
    have hc := h₁ 997 (by omega)
    norm_num [h₀ 1002 (by omega), ha] at hc
    exact ⟨hc, hb⟩
  have node_below_threshold_formula : ∀ k : ℕ, f (999 - (k : ℤ)) = if Even k then 998 else 997 := by
    have h999 := h₁ 999 (by omega)
    norm_num [h₀ 1004 (by omega), h₀ 1001 (by omega)] at h999
    have h996 := h₁ 996 (by omega)
    norm_num [h₀ 1001 (by omega), node_closed_two_cycle.2] at h996
    have h995 := h₁ 995 (by omega)
    norm_num [h₀ 1000 (by omega), node_closed_two_cycle.1] at h995
    intro k
    induction k using Nat.strong_induction_on with
    | h k ih =>
      by_cases hk : k < 5
      · interval_cases k <;> norm_num [h999, h996, h995, node_closed_two_cycle.1, node_closed_two_cycle.2]
      · have hk5 : 5 ≤ k := by omega
        have hi := ih (k - 5) (by omega)
        have hidx : (999 : ℤ) - (k : ℤ) + 5 = 999 - ((k - 5 : ℕ) : ℤ) := by omega
        rw [h₁ (999 - (k : ℤ)) (by omega), hidx, hi]
        by_cases he : Even k
        · have hkmod := Nat.even_iff.mp he
          have ho : ¬ Even (k - 5) := Nat.not_even_iff.mpr (by omega)
          simp [he, ho, node_closed_two_cycle.1]
        · have hkmod := Nat.not_even_iff.mp he
          have ho : Even (k - 5) := Nat.even_iff.mpr (by omega)
          simp [he, ho, node_closed_two_cycle.2]
  have node_root : f 84 = 997 := by
    have h := node_below_threshold_formula 915
    norm_num at h
    exact h
  exact node_root

#print axioms aime_1984_p7
