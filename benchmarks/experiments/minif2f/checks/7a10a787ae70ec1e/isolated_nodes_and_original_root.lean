import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ)
  (h₀ : 12 * Complex.normSq z = 2 * Complex.normSq (z + 2) + Complex.normSq (z^2 + 1) + 31) : z.re = -1 := by
  have he := h₀
  norm_num [Complex.normSq_apply, pow_two] at he
  have hs : (z.re + 1) ^ 2 = 0 := by
    nlinarith [sq_nonneg (z.re ^ 2 + z.im ^ 2 - 6), sq_nonneg (z.re + 1)]
  have hz := sq_eq_zero_iff.mp hs
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ)
  (h₀ : 12 * Complex.normSq z = 2 * Complex.normSq (z + 2) + Complex.normSq (z^2 + 1) + 31) : Complex.normSq z = 6 := by
  have he := h₀
  norm_num [Complex.normSq_apply, pow_two] at he
  have hs : (z.re ^ 2 + z.im ^ 2 - 6) ^ 2 = 0 := by
    nlinarith [sq_nonneg (z.re + 1), sq_nonneg (z.re ^ 2 + z.im ^ 2 - 6)]
  have hz := sq_eq_zero_iff.mp hs
  simpa [Complex.normSq_apply, pow_two] using (show z.re ^ 2 + z.im ^ 2 = 6 by linarith)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ)
  (h₀ : 12 * Complex.normSq z = 2 * Complex.normSq (z + 2) + Complex.normSq (z^2 + 1) + 31) (node_real_part : z.re = -1) (node_norm_square : Complex.normSq z = 6) : z + 6 / z = -2 := by
  have hz : z ≠ 0 := by
    intro hz
    rw [hz] at node_norm_square
    norm_num [Complex.normSq_apply] at node_norm_square
  have hs : z.re ^ 2 + z.im ^ 2 = 6 := by
    simpa [Complex.normSq_apply, pow_two] using node_norm_square
  rw [node_real_part] at hs
  norm_num at hs
  field_simp [hz]
  apply Complex.ext <;> simp [pow_two, Complex.mul_re, Complex.mul_im, node_real_part] <;> nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12b_2021_p18 (z : ℂ)
  (h₀ : 12 * Complex.normSq z = 2 * Complex.normSq (z + 2) + Complex.normSq (z^2 + 1) + 31) : z + 6 / z = -2 := by
  have node_real_part : z.re = -1 := by
    have he := h₀
    norm_num [Complex.normSq_apply, pow_two] at he
    have hs : (z.re + 1) ^ 2 = 0 := by
      nlinarith [sq_nonneg (z.re ^ 2 + z.im ^ 2 - 6), sq_nonneg (z.re + 1)]
    have hz := sq_eq_zero_iff.mp hs
    linarith
  have node_norm_square : Complex.normSq z = 6 := by
    have he := h₀
    norm_num [Complex.normSq_apply, pow_two] at he
    have hs : (z.re ^ 2 + z.im ^ 2 - 6) ^ 2 = 0 := by
      nlinarith [sq_nonneg (z.re + 1), sq_nonneg (z.re ^ 2 + z.im ^ 2 - 6)]
    have hz := sq_eq_zero_iff.mp hs
    simpa [Complex.normSq_apply, pow_two] using (show z.re ^ 2 + z.im ^ 2 = 6 by linarith)
  have node_root : z + 6 / z = -2 := by
    have hz : z ≠ 0 := by
      intro hz
      rw [hz] at node_norm_square
      norm_num [Complex.normSq_apply] at node_norm_square
    have hs : z.re ^ 2 + z.im ^ 2 = 6 := by
      simpa [Complex.normSq_apply, pow_two] using node_norm_square
    rw [node_real_part] at hs
    norm_num at hs
    field_simp [hz]
    apply Complex.ext <;> simp [pow_two, Complex.mul_re, Complex.mul_im, node_real_part] <;> nlinarith
  exact node_root

#print axioms amc12b_2021_p18
