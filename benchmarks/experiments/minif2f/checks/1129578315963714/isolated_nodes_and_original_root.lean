import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ)
  (h₀ : y^2 + 3 * (x^2 * y^2) = 30 * x^2 + 517) : 16 ≤ y ^ 2 := by
  have hp : 10 < y ^ 2 := by
    by_contra hh
    have hm := mul_nonneg (show 0 ≤ 3 * x ^ 2 + 1 by positivity) (show 0 ≤ 10 - y ^ 2 by omega)
    nlinarith
  by_contra hh
  have hl : -3 ≤ y := by nlinarith [sq_nonneg (y + 4)]
  have hu : y ≤ 3 := by nlinarith [sq_nonneg (y - 4)]
  interval_cases y <;> norm_num at hp

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ)
  (h₀ : y^2 + 3 * (x^2 * y^2) = 30 * x^2 + 517) (node_integer_square_lower_bound : 16 ≤ y ^ 2) : x ^ 2 ≤ 27 := by
  have hm := mul_nonneg (sq_nonneg x) (sub_nonneg.mpr node_integer_square_lower_bound)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ)
  (h₀ : y^2 + 3 * (x^2 * y^2) = 30 * x^2 + 517) (node_bounded_first_square : x ^ 2 ≤ 27) : 3 * (x^2 * y^2) = 588 := by
  have hl : -5 ≤ x := by nlinarith [sq_nonneg (x + 6)]
  have hu : x ≤ 5 := by nlinarith [sq_nonneg (x - 6)]
  interval_cases x <;> norm_num at * <;> (try ring_nf at h₀) <;> try omega
  all_goals
    have hylo : -22 ≤ y := by nlinarith [sq_nonneg (y + 23)]
    have hyhi : y ≤ 22 := by nlinarith [sq_nonneg (y - 23)]
    interval_cases y <;> norm_num at *

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1987_p5 (x y : ℤ)
  (h₀ : y^2 + 3 * (x^2 * y^2) = 30 * x^2 + 517) : 3 * (x^2 * y^2) = 588 := by
  have node_integer_square_lower_bound : 16 ≤ y ^ 2 := by
    have hp : 10 < y ^ 2 := by
      by_contra hh
      have hm := mul_nonneg (show 0 ≤ 3 * x ^ 2 + 1 by positivity) (show 0 ≤ 10 - y ^ 2 by omega)
      nlinarith
    by_contra hh
    have hl : -3 ≤ y := by nlinarith [sq_nonneg (y + 4)]
    have hu : y ≤ 3 := by nlinarith [sq_nonneg (y - 4)]
    interval_cases y <;> norm_num at hp
  have node_bounded_first_square : x ^ 2 ≤ 27 := by
    have hm := mul_nonneg (sq_nonneg x) (sub_nonneg.mpr node_integer_square_lower_bound)
    nlinarith
  have node_root : 3 * (x^2 * y^2) = 588 := by
    have hl : -5 ≤ x := by nlinarith [sq_nonneg (x + 6)]
    have hu : x ≤ 5 := by nlinarith [sq_nonneg (x - 6)]
    interval_cases x <;> norm_num at * <;> (try ring_nf at h₀) <;> try omega
    all_goals
      have hylo : -22 ≤ y := by nlinarith [sq_nonneg (y + 23)]
      have hyhi : y ≤ 22 := by nlinarith [sq_nonneg (y - 23)]
      interval_cases y <;> norm_num at *
  exact node_root

#print axioms aime_1987_p5
