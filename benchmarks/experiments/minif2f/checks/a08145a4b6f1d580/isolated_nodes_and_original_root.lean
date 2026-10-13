import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ) (h₀ : z = (1 + Complex.I) / Real.sqrt 2) : z ^ 2 = Complex.I := by
  have hs : (Real.sqrt 2) ^ 2 = (2 : ℝ) := Real.sq_sqrt (by norm_num)
  have hc : ((Real.sqrt 2 : ℝ) : ℂ) ^ 2 = (2 : ℂ) := by exact_mod_cast hs
  rw [h₀, div_pow, hc]
  norm_num [add_sq, Complex.I_sq]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ) (h₀ : z = (1 + Complex.I) / Real.sqrt 2) (node_unit_square : z ^ 2 = Complex.I) : (∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) = 6 * z := by
  have hz4 : z ^ 4 = -1 := by
    calc z ^ 4 = (z ^ 2) ^ 2 := by ring
         _ = -1 := by rw [node_unit_square]; norm_num
  have hz8 : z ^ 8 = 1 := by
    calc z ^ 8 = (z ^ 4) ^ 2 := by ring
         _ = 1 := by rw [hz4]; norm_num
  calc
    (∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) = ∑ k ∈ Finset.Icc 1 12, z ^ (k ^ 2 % 8) := by
      apply Finset.sum_congr rfl
      intro k hk
      exact pow_eq_pow_mod (k ^ 2) hz8
    _ = 6 * z := by norm_num [Finset.sum_Icc_succ_top, hz4]; ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ) (h₀ : z = (1 + Complex.I) / Real.sqrt 2) (node_unit_square : z ^ 2 = Complex.I) : (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2) = 6 / z := by
  have hz4 : z ^ 4 = -1 := by
    calc z ^ 4 = (z ^ 2) ^ 2 := by ring
         _ = -1 := by rw [node_unit_square]; norm_num
  have hz8 : z ^ 8 = 1 := by
    calc z ^ 8 = (z ^ 4) ^ 2 := by ring
         _ = 1 := by rw [hz4]; norm_num
  calc
    (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2) = ∑ k ∈ Finset.Icc 1 12, 1 / z ^ (k ^ 2 % 8) := by
      apply Finset.sum_congr rfl
      intro k hk
      rw [pow_eq_pow_mod (k ^ 2) hz8]
    _ = 6 / z := by norm_num [Finset.sum_Icc_succ_top, hz4]; ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (z : ℂ) (h₀ : z = (1 + Complex.I) / Real.sqrt 2) (node_unit_square : z ^ 2 = Complex.I) (node_forward_gauss_sum : (∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) = 6 * z) (node_reciprocal_gauss_sum : (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2) = 6 / z) : ((∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) * (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2)) = 36 := by
  have hz : z ≠ 0 := by
    intro he
    have hi := node_unit_square
    simp [he] at hi
    exact Complex.I_ne_zero hi.symm
  rw [node_forward_gauss_sum, node_reciprocal_gauss_sum]
  field_simp
  ; ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2019_p21 (z : ℂ) (h₀ : z = (1 + Complex.I) / Real.sqrt 2) : ((∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) * (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2)) = 36 := by
  have node_unit_square : z ^ 2 = Complex.I := by
    have hs : (Real.sqrt 2) ^ 2 = (2 : ℝ) := Real.sq_sqrt (by norm_num)
    have hc : ((Real.sqrt 2 : ℝ) : ℂ) ^ 2 = (2 : ℂ) := by exact_mod_cast hs
    rw [h₀, div_pow, hc]
    norm_num [add_sq, Complex.I_sq]
  have node_forward_gauss_sum : (∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) = 6 * z := by
    have hz4 : z ^ 4 = -1 := by
      calc z ^ 4 = (z ^ 2) ^ 2 := by ring
           _ = -1 := by rw [node_unit_square]; norm_num
    have hz8 : z ^ 8 = 1 := by
      calc z ^ 8 = (z ^ 4) ^ 2 := by ring
           _ = 1 := by rw [hz4]; norm_num
    calc
      (∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) = ∑ k ∈ Finset.Icc 1 12, z ^ (k ^ 2 % 8) := by
        apply Finset.sum_congr rfl
        intro k hk
        exact pow_eq_pow_mod (k ^ 2) hz8
      _ = 6 * z := by norm_num [Finset.sum_Icc_succ_top, hz4]; ring
  have node_reciprocal_gauss_sum : (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2) = 6 / z := by
    have hz4 : z ^ 4 = -1 := by
      calc z ^ 4 = (z ^ 2) ^ 2 := by ring
           _ = -1 := by rw [node_unit_square]; norm_num
    have hz8 : z ^ 8 = 1 := by
      calc z ^ 8 = (z ^ 4) ^ 2 := by ring
           _ = 1 := by rw [hz4]; norm_num
    calc
      (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2) = ∑ k ∈ Finset.Icc 1 12, 1 / z ^ (k ^ 2 % 8) := by
        apply Finset.sum_congr rfl
        intro k hk
        rw [pow_eq_pow_mod (k ^ 2) hz8]
      _ = 6 / z := by norm_num [Finset.sum_Icc_succ_top, hz4]; ring
  have node_root : ((∑ k ∈ Finset.Icc 1 12, z ^ k ^ 2) * (∑ k ∈ Finset.Icc 1 12, 1 / z ^ k ^ 2)) = 36 := by
    have hz : z ≠ 0 := by
      intro he
      have hi := node_unit_square
      simp [he] at hi
      exact Complex.I_ne_zero hi.symm
    rw [node_forward_gauss_sum, node_reciprocal_gauss_sum]
    field_simp
    ; ring
  exact node_root

#print axioms amc12a_2019_p21
