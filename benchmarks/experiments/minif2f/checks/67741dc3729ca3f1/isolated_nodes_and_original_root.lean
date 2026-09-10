import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (h₀ : 0 < a)
  (h₁ : 1 / a - Int.floor (1 / a) = a^2 - Int.floor (a^2))
  (h₂ : 2 < a^2)
  (h₃ : a^2 < 3) : Int.floor (1 / a) = 0 ∧ Int.floor (a ^ 2) = 2 := by
  have ha : 1 < a := by
    by_contra hh
    have hb : a ≤ 1 := le_of_not_gt hh
    have hp := mul_nonneg (sub_nonneg.mpr hb) (show 0 ≤ 1 + a by linarith)
    nlinarith
  constructor
  · rw [Int.floor_eq_iff]
    norm_num
    exact ⟨by positivity, by simpa only [one_div] using (div_lt_one h₀).2 ha⟩
  · rw [Int.floor_eq_iff]
    norm_num
    exact ⟨h₂.le, h₃⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (h₀ : 0 < a)
  (h₁ : 1 / a - Int.floor (1 / a) = a^2 - Int.floor (a^2))
  (h₂ : 2 < a^2)
  (h₃ : a^2 < 3) (node_integer_parts : Int.floor (1 / a) = 0 ∧ Int.floor (a ^ 2) = 2) : a ^ 2 - a - 1 = 0 := by
  have hi : 1 / a = a ^ 2 - 2 := by
    have he := h₁
    rw [node_integer_parts.1, node_integer_parts.2] at he
    norm_num at he
    simpa only [one_div] using he
  have he := (div_eq_iff h₀.ne').mp hi
  have hz : (a + 1) * (a ^ 2 - a - 1) = 0 := by nlinarith
  exact (mul_eq_zero.mp hz).resolve_left (by linarith)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (h₀ : 0 < a)
  (h₁ : 1 / a - Int.floor (1 / a) = a^2 - Int.floor (a^2))
  (h₂ : 2 < a^2)
  (h₃ : a^2 < 3) (node_quadratic_relation : a ^ 2 - a - 1 = 0) : a^12 - 144 * (1 / a) = 233 := by
  have hi : 1 / a = a - 1 := by
    apply (div_eq_iff h₀.ne').2
    nlinarith [node_quadratic_relation]
  rw [hi]
  linear_combination (a ^ 10 + a ^ 9 + 2 * a ^ 8 + 3 * a ^ 7 + 5 * a ^ 6 + 8 * a ^ 5 + 13 * a ^ 4 + 21 * a ^ 3 + 34 * a ^ 2 + 55 * a + 89) * node_quadratic_relation

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1997_p9 (a : ℝ)
  (h₀ : 0 < a)
  (h₁ : 1 / a - Int.floor (1 / a) = a^2 - Int.floor (a^2))
  (h₂ : 2 < a^2)
  (h₃ : a^2 < 3) : a^12 - 144 * (1 / a) = 233 := by
  have node_integer_parts : Int.floor (1 / a) = 0 ∧ Int.floor (a ^ 2) = 2 := by
    have ha : 1 < a := by
      by_contra hh
      have hb : a ≤ 1 := le_of_not_gt hh
      have hp := mul_nonneg (sub_nonneg.mpr hb) (show 0 ≤ 1 + a by linarith)
      nlinarith
    constructor
    · rw [Int.floor_eq_iff]
      norm_num
      exact ⟨by positivity, by simpa only [one_div] using (div_lt_one h₀).2 ha⟩
    · rw [Int.floor_eq_iff]
      norm_num
      exact ⟨h₂.le, h₃⟩
  have node_quadratic_relation : a ^ 2 - a - 1 = 0 := by
    have hi : 1 / a = a ^ 2 - 2 := by
      have he := h₁
      rw [node_integer_parts.1, node_integer_parts.2] at he
      norm_num at he
      simpa only [one_div] using he
    have he := (div_eq_iff h₀.ne').mp hi
    have hz : (a + 1) * (a ^ 2 - a - 1) = 0 := by nlinarith
    exact (mul_eq_zero.mp hz).resolve_left (by linarith)
  have node_root : a^12 - 144 * (1 / a) = 233 := by
    have hi : 1 / a = a - 1 := by
      apply (div_eq_iff h₀.ne').2
      nlinarith [node_quadratic_relation]
    rw [hi]
    linear_combination (a ^ 10 + a ^ 9 + 2 * a ^ 8 + 3 * a ^ 7 + 5 * a ^ 6 + 8 * a ^ 5 + 13 * a ^ 4 + 21 * a ^ 3 + 34 * a ^ 2 + 55 * a + 89) * node_quadratic_relation
  exact node_root

#print axioms aime_1997_p9
