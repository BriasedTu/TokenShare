import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x a : ℕ → ℝ) (h₀ : a 1 ≠ a 2) (h₁ : a 1 ≠ a 3) (h₂ : a 1 ≠ a 4)
  (h₃ : a 2 ≠ a 3) (h₄ : a 2 ≠ a 4) (h₅ : a 3 ≠ a 4) (h₆ : a 1 > a 2) (h₇ : a 2 > a 3)
  (h₈ : a 3 > a 4)
  (h₉ : abs (a 1 - a 2) * x 2 + abs (a 1 - a 3) * x 3 + abs (a 1 - a 4) * x 4 = 1)
  (h₁₀ : abs (a 2 - a 1) * x 1 + abs (a 2 - a 3) * x 3 + abs (a 2 - a 4) * x 4 = 1)
  (h₁₁ : abs (a 3 - a 1) * x 1 + abs (a 3 - a 2) * x 2 + abs (a 3 - a 4) * x 4 = 1)
  (h₁₂ : abs (a 4 - a 1) * x 1 + abs (a 4 - a 2) * x 2 + abs (a 4 - a 3) * x 3 = 1) : (-x 1 + x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 - x 3 + x 4 = 0) := by
  have h13 : a 3 < a 1 := by linarith
  have h14 : a 4 < a 1 := by linarith
  have h24 : a 4 < a 2 := by linarith
  simp only [abs_of_pos (sub_pos.mpr h₆), abs_of_neg (sub_neg.mpr h₆), abs_of_pos (sub_pos.mpr h₇), abs_of_neg (sub_neg.mpr h₇), abs_of_pos (sub_pos.mpr h₈), abs_of_neg (sub_neg.mpr h₈), abs_of_pos (sub_pos.mpr h13), abs_of_neg (sub_neg.mpr h13), abs_of_pos (sub_pos.mpr h14), abs_of_neg (sub_neg.mpr h14), abs_of_pos (sub_pos.mpr h24), abs_of_neg (sub_neg.mpr h24)] at h₉ h₁₀ h₁₁ h₁₂
  have e1 : (a 1 - a 2) * (-x 1 + x 2 + x 3 + x 4) = 0 := by nlinarith only [h₉, h₁₀]
  have e2 : (a 2 - a 3) * (-x 1 - x 2 + x 3 + x 4) = 0 := by nlinarith only [h₁₀, h₁₁]
  have e3 : (a 3 - a 4) * (-x 1 - x 2 - x 3 + x 4) = 0 := by nlinarith only [h₁₁, h₁₂]
  exact ⟨(mul_eq_zero.mp e1).resolve_left (sub_ne_zero.mpr h₀), (mul_eq_zero.mp e2).resolve_left (sub_ne_zero.mpr h₃), (mul_eq_zero.mp e3).resolve_left (sub_ne_zero.mpr h₅)⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x a : ℕ → ℝ) (h₀ : a 1 ≠ a 2) (h₁ : a 1 ≠ a 3) (h₂ : a 1 ≠ a 4)
  (h₃ : a 2 ≠ a 3) (h₄ : a 2 ≠ a 4) (h₅ : a 3 ≠ a 4) (h₆ : a 1 > a 2) (h₇ : a 2 > a 3)
  (h₈ : a 3 > a 4)
  (h₉ : abs (a 1 - a 2) * x 2 + abs (a 1 - a 3) * x 3 + abs (a 1 - a 4) * x 4 = 1)
  (h₁₀ : abs (a 2 - a 1) * x 1 + abs (a 2 - a 3) * x 3 + abs (a 2 - a 4) * x 4 = 1)
  (h₁₁ : abs (a 3 - a 1) * x 1 + abs (a 3 - a 2) * x 2 + abs (a 3 - a 4) * x 4 = 1)
  (h₁₂ : abs (a 4 - a 1) * x 1 + abs (a 4 - a 2) * x 2 + abs (a 4 - a 3) * x 3 = 1) (node_successive_row_differences : (-x 1 + x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 - x 3 + x 4 = 0)) : x 2 = 0 ∧ x 3 = 0 ∧ x 1 = 1 / abs (a 1 - a 4) ∧ x 4 = 1 / abs (a 1 - a 4) := by
  rcases node_successive_row_differences with ⟨e1, e2, e3⟩
  have hx2 : x 2 = 0 := by linarith
  have hx3 : x 3 = 0 := by linarith
  have hx1 : x 1 = x 4 := by linarith
  have hn : abs (a 1 - a 4) ≠ 0 := abs_ne_zero.mpr (sub_ne_zero.mpr h₂)
  have hx4 : x 4 = 1 / abs (a 1 - a 4) := by
    apply (eq_div_iff hn).2
    rw [hx2, hx3] at h₉
    nlinarith [h₉]
  exact ⟨hx2, hx3, hx1.trans hx4, hx4⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1966_p5 (x a : ℕ → ℝ) (h₀ : a 1 ≠ a 2) (h₁ : a 1 ≠ a 3) (h₂ : a 1 ≠ a 4)
  (h₃ : a 2 ≠ a 3) (h₄ : a 2 ≠ a 4) (h₅ : a 3 ≠ a 4) (h₆ : a 1 > a 2) (h₇ : a 2 > a 3)
  (h₈ : a 3 > a 4)
  (h₉ : abs (a 1 - a 2) * x 2 + abs (a 1 - a 3) * x 3 + abs (a 1 - a 4) * x 4 = 1)
  (h₁₀ : abs (a 2 - a 1) * x 1 + abs (a 2 - a 3) * x 3 + abs (a 2 - a 4) * x 4 = 1)
  (h₁₁ : abs (a 3 - a 1) * x 1 + abs (a 3 - a 2) * x 2 + abs (a 3 - a 4) * x 4 = 1)
  (h₁₂ : abs (a 4 - a 1) * x 1 + abs (a 4 - a 2) * x 2 + abs (a 4 - a 3) * x 3 = 1) : x 2 = 0 ∧ x 3 = 0 ∧ x 1 = 1 / abs (a 1 - a 4) ∧ x 4 = 1 / abs (a 1 - a 4) := by
  have node_successive_row_differences : (-x 1 + x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 + x 3 + x 4 = 0) ∧ (-x 1 - x 2 - x 3 + x 4 = 0) := by
    have h13 : a 3 < a 1 := by linarith
    have h14 : a 4 < a 1 := by linarith
    have h24 : a 4 < a 2 := by linarith
    simp only [abs_of_pos (sub_pos.mpr h₆), abs_of_neg (sub_neg.mpr h₆), abs_of_pos (sub_pos.mpr h₇), abs_of_neg (sub_neg.mpr h₇), abs_of_pos (sub_pos.mpr h₈), abs_of_neg (sub_neg.mpr h₈), abs_of_pos (sub_pos.mpr h13), abs_of_neg (sub_neg.mpr h13), abs_of_pos (sub_pos.mpr h14), abs_of_neg (sub_neg.mpr h14), abs_of_pos (sub_pos.mpr h24), abs_of_neg (sub_neg.mpr h24)] at h₉ h₁₀ h₁₁ h₁₂
    have e1 : (a 1 - a 2) * (-x 1 + x 2 + x 3 + x 4) = 0 := by nlinarith only [h₉, h₁₀]
    have e2 : (a 2 - a 3) * (-x 1 - x 2 + x 3 + x 4) = 0 := by nlinarith only [h₁₀, h₁₁]
    have e3 : (a 3 - a 4) * (-x 1 - x 2 - x 3 + x 4) = 0 := by nlinarith only [h₁₁, h₁₂]
    exact ⟨(mul_eq_zero.mp e1).resolve_left (sub_ne_zero.mpr h₀), (mul_eq_zero.mp e2).resolve_left (sub_ne_zero.mpr h₃), (mul_eq_zero.mp e3).resolve_left (sub_ne_zero.mpr h₅)⟩
  have node_root : x 2 = 0 ∧ x 3 = 0 ∧ x 1 = 1 / abs (a 1 - a 4) ∧ x 4 = 1 / abs (a 1 - a 4) := by
    rcases node_successive_row_differences with ⟨e1, e2, e3⟩
    have hx2 : x 2 = 0 := by linarith
    have hx3 : x 3 = 0 := by linarith
    have hx1 : x 1 = x 4 := by linarith
    have hn : abs (a 1 - a 4) ≠ 0 := abs_ne_zero.mpr (sub_ne_zero.mpr h₂)
    have hx4 : x 4 = 1 / abs (a 1 - a 4) := by
      apply (eq_div_iff hn).2
      rw [hx2, hx3] at h₉
      nlinarith [h₉]
    exact ⟨hx2, hx3, hx1.trans hx4, hx4⟩
  exact node_root

#print axioms imo_1966_p5
