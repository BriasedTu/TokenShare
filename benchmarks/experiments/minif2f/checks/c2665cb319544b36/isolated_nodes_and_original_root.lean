import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℂ)
  (h₀ : a^3 - 8 = 0)
  (h₁ : b^3 - 8 * b^2 - 8 * b + 64 = 0) : ‖a‖=2 ∧ -1 ≤ a.re := by
  have he : a^3=8 := sub_eq_zero.mp h₀
  have hn := congrArg norm he
  norm_num [norm_pow] at hn
  have hn2 : ‖a‖=2 := by nlinarith [norm_nonneg a, sq_nonneg (‖a‖-2)]
  refine ⟨hn2, ?_⟩
  have hf : (a-2)*(a^2+2*a+4)=0 := by linear_combination h₀
  rcases mul_eq_zero.mp hf with hp | hq
  · have ha : a=2 := sub_eq_zero.mp hp
    norm_num [ha]
  · have hr := congrArg Complex.re hq
    have hi := congrArg Complex.im hq
    simp [pow_two, Complex.mul_re, Complex.mul_im] at hr hi
    have him : a.im ≠ 0 := by intro hz; rw [hz] at hr; nlinarith [sq_nonneg (a.re+1)]
    have hz : (a.re+1)*a.im=0 := by nlinarith
    have hx := (mul_eq_zero.mp hz).resolve_right him
    linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℂ)
  (h₀ : a^3 - 8 = 0)
  (h₁ : b^3 - 8 * b^2 - 8 * b + 64 = 0) : b=8 ∨ ‖b‖ ≤ 3 := by
  have hf : (b-8)*(b^2-8)=0 := by linear_combination h₁
  rcases mul_eq_zero.mp hf with hp | hq
  · exact Or.inl (sub_eq_zero.mp hp)
  · right
    have he : b^2=8 := sub_eq_zero.mp hq
    have hn := congrArg norm he
    norm_num [norm_pow] at hn
    nlinarith [norm_nonneg b]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℂ)
  (h₀ : a^3 - 8 = 0)
  (h₁ : b^3 - 8 * b^2 - 8 * b + 64 = 0) (node_first_root_geometry : ‖a‖=2 ∧ -1 ≤ a.re) (node_second_root_classification : b=8 ∨ ‖b‖ ≤ 3) : ‖a - b‖ ≤ 2 * Real.sqrt 21 := by
  have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 21)
  have hp := Real.sqrt_nonneg (21:ℝ)
  rcases node_second_root_classification with hb | hb
  · rw [hb]
    have ha : a.re^2+a.im^2=4 := by have := Complex.normSq_eq_norm_sq a; rw [node_first_root_geometry.1] at this; norm_num [Complex.normSq_apply] at this; nlinarith
    have hd := Complex.normSq_eq_norm_sq (a-8)
    simp [Complex.normSq_apply, Complex.sub_re, Complex.sub_im] at hd
    nlinarith [node_first_root_geometry.2, norm_nonneg (a-8)]
  · have ht := norm_sub_le a b
    rw [node_first_root_geometry.1] at ht
    nlinarith [norm_nonneg (a-b)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2020_p15 (a b : ℂ)
  (h₀ : a^3 - 8 = 0)
  (h₁ : b^3 - 8 * b^2 - 8 * b + 64 = 0) : ‖a - b‖ ≤ 2 * Real.sqrt 21 := by
  have node_first_root_geometry : ‖a‖=2 ∧ -1 ≤ a.re := by
    have he : a^3=8 := sub_eq_zero.mp h₀
    have hn := congrArg norm he
    norm_num [norm_pow] at hn
    have hn2 : ‖a‖=2 := by nlinarith [norm_nonneg a, sq_nonneg (‖a‖-2)]
    refine ⟨hn2, ?_⟩
    have hf : (a-2)*(a^2+2*a+4)=0 := by linear_combination h₀
    rcases mul_eq_zero.mp hf with hp | hq
    · have ha : a=2 := sub_eq_zero.mp hp
      norm_num [ha]
    · have hr := congrArg Complex.re hq
      have hi := congrArg Complex.im hq
      simp [pow_two, Complex.mul_re, Complex.mul_im] at hr hi
      have him : a.im ≠ 0 := by intro hz; rw [hz] at hr; nlinarith [sq_nonneg (a.re+1)]
      have hz : (a.re+1)*a.im=0 := by nlinarith
      have hx := (mul_eq_zero.mp hz).resolve_right him
      linarith
  have node_second_root_classification : b=8 ∨ ‖b‖ ≤ 3 := by
    have hf : (b-8)*(b^2-8)=0 := by linear_combination h₁
    rcases mul_eq_zero.mp hf with hp | hq
    · exact Or.inl (sub_eq_zero.mp hp)
    · right
      have he : b^2=8 := sub_eq_zero.mp hq
      have hn := congrArg norm he
      norm_num [norm_pow] at hn
      nlinarith [norm_nonneg b]
  have node_root : ‖a - b‖ ≤ 2 * Real.sqrt 21 := by
    have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 21)
    have hp := Real.sqrt_nonneg (21:ℝ)
    rcases node_second_root_classification with hb | hb
    · rw [hb]
      have ha : a.re^2+a.im^2=4 := by have := Complex.normSq_eq_norm_sq a; rw [node_first_root_geometry.1] at this; norm_num [Complex.normSq_apply] at this; nlinarith
      have hd := Complex.normSq_eq_norm_sq (a-8)
      simp [Complex.normSq_apply, Complex.sub_re, Complex.sub_im] at hd
      nlinarith [node_first_root_geometry.2, norm_nonneg (a-8)]
    · have ht := norm_sub_le a b
      rw [node_first_root_geometry.1] at ht
      nlinarith [norm_nonneg (a-b)]
  exact node_root

#print axioms amc12a_2020_p15
