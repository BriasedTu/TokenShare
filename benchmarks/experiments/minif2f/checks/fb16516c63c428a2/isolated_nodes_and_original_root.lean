import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) : (a + b) ^ 2 ≤ 2 * (a ^ 2 + b ^ 2) := by
  nlinarith [sq_nonneg (a - b)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) : (a ^ 2 + b ^ 2) ^ 2 ≤ 2 * (a ^ 4 + b ^ 4) := by
  nlinarith [sq_nonneg (a ^ 2 - b ^ 2)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) (node_sum_square_bound : (a + b) ^ 2 ≤ 2 * (a ^ 2 + b ^ 2)) (node_squares_square_bound : (a ^ 2 + b ^ 2) ^ 2 ≤ 2 * (a ^ 4 + b ^ 4)) : (a + b) ^ 4 ≤ 8 * (a ^ 4 + b ^ 4) := by
  have h := mul_self_le_mul_self (sq_nonneg (a + b)) node_sum_square_bound
  nlinarith [sq_nonneg (a ^ 2 + b ^ 2)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_apb4leq8ta4pb4 (a b : ℝ) (h₀ : 0 < a ∧ 0 < b) : (a + b) ^ 4 ≤ 8 * (a ^ 4 + b ^ 4) := by
  have node_sum_square_bound : (a + b) ^ 2 ≤ 2 * (a ^ 2 + b ^ 2) := by
    nlinarith [sq_nonneg (a - b)]
  have node_squares_square_bound : (a ^ 2 + b ^ 2) ^ 2 ≤ 2 * (a ^ 4 + b ^ 4) := by
    nlinarith [sq_nonneg (a ^ 2 - b ^ 2)]
  have node_root : (a + b) ^ 4 ≤ 8 * (a ^ 4 + b ^ 4) := by
    have h := mul_self_le_mul_self (sq_nonneg (a + b)) node_sum_square_bound
    nlinarith [sq_nonneg (a ^ 2 + b ^ 2)]
  exact node_root

#print axioms algebra_apb4leq8ta4pb4
