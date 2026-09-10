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
  (h₀ : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) + Real.sqrt (1 + Real.sqrt (1 + a)) = 6) : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) = 2 * Real.sqrt (1 + Real.sqrt (1 + a)) := by
  have hi : Real.sqrt (16 + 16 * a) = 4 * Real.sqrt (1 + a) := by
    rw [show 16 + 16 * a = 16 * (1 + a) by ring, Real.sqrt_mul (by norm_num : (0 : ℝ) ≤ 16)]
    norm_num
  rw [hi, show 4 + 4 * Real.sqrt (1 + a) = 4 * (1 + Real.sqrt (1 + a)) by ring,
    Real.sqrt_mul (by norm_num : (0 : ℝ) ≤ 4)]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (h₀ : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) + Real.sqrt (1 + Real.sqrt (1 + a)) = 6) (node_normalize_nested_radicals : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) = 2 * Real.sqrt (1 + Real.sqrt (1 + a))) : Real.sqrt (1 + a) = 3 := by
  have he : Real.sqrt (1 + Real.sqrt (1 + a)) = 2 := by linarith [node_normalize_nested_radicals]
  have hs := Real.sq_sqrt (show 0 ≤ 1 + Real.sqrt (1 + a) by positivity)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (h₀ : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) + Real.sqrt (1 + Real.sqrt (1 + a)) = 6) (node_inner_radical_value : Real.sqrt (1 + a) = 3) : a = 8 := by
  have hp : 0 < 1 + a := (Real.sqrt_pos.mp (by rw [node_inner_radical_value]; norm_num))
  have hs := Real.sq_sqrt hp.le
  nlinarith [node_inner_radical_value]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_17 (a : ℝ)
  (h₀ : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) + Real.sqrt (1 + Real.sqrt (1 + a)) = 6) : a = 8 := by
  have node_normalize_nested_radicals : Real.sqrt (4 + Real.sqrt (16 + 16 * a)) = 2 * Real.sqrt (1 + Real.sqrt (1 + a)) := by
    have hi : Real.sqrt (16 + 16 * a) = 4 * Real.sqrt (1 + a) := by
      rw [show 16 + 16 * a = 16 * (1 + a) by ring, Real.sqrt_mul (by norm_num : (0 : ℝ) ≤ 16)]
      norm_num
    rw [hi, show 4 + 4 * Real.sqrt (1 + a) = 4 * (1 + Real.sqrt (1 + a)) by ring,
      Real.sqrt_mul (by norm_num : (0 : ℝ) ≤ 4)]
    norm_num
  have node_inner_radical_value : Real.sqrt (1 + a) = 3 := by
    have he : Real.sqrt (1 + Real.sqrt (1 + a)) = 2 := by linarith [node_normalize_nested_radicals]
    have hs := Real.sq_sqrt (show 0 ≤ 1 + Real.sqrt (1 + a) by positivity)
    nlinarith
  have node_root : a = 8 := by
    have hp : 0 < 1 + a := (Real.sqrt_pos.mp (by rw [node_inner_radical_value]; norm_num))
    have hs := Real.sq_sqrt hp.le
    nlinarith [node_inner_radical_value]
  exact node_root

#print axioms mathd_algebra_17
