import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : a ≤ b ∧ b ≤ c)
  (h₁ : a + b + c = 2)
  (h₂ : a * b + b * c + c * a = 1) : 0 ≤ a ∧ c ≤ 4 / 3 := by
  have hs := congrArg (fun t : ℝ => t ^ 2) h₁
  have ha := congrArg (fun t : ℝ => a * t) h₁
  have hc := congrArg (fun t : ℝ => c * t) h₁
  constructor
  · nlinarith [sq_nonneg (b - c), sq_nonneg a]
  · nlinarith [sq_nonneg (a - b), sq_nonneg (c - 4 / 3)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : a ≤ b ∧ b ≤ c)
  (h₁ : a + b + c = 2)
  (h₂ : a * b + b * c + c * a = 1) : a ≤ 1 / 3 := by
  have ha := congrArg (fun t : ℝ => a * t) h₁
  have hprod := mul_nonneg (sub_nonneg.mpr h₀.1) (sub_nonneg.mpr (le_trans h₀.1 h₀.2))
  by_contra hh
  have hl : 1 / 3 < a := lt_of_not_ge hh
  have hu : a < 1 := by linarith [h₀.1, h₀.2]
  have hp := mul_pos (sub_pos.mpr hl) (sub_pos.mpr hu)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : a ≤ b ∧ b ≤ c)
  (h₁ : a + b + c = 2)
  (h₂ : a * b + b * c + c * a = 1) : 1 ≤ c := by
  have hc := congrArg (fun t : ℝ => c * t) h₁
  have hprod := mul_nonneg (sub_nonneg.mpr (le_trans h₀.1 h₀.2)) (sub_nonneg.mpr h₀.2)
  by_contra hh
  have hu : c < 1 := lt_of_not_ge hh
  have hl : 1 / 3 < c := by linarith [h₀.1, h₀.2]
  have hp := mul_pos (sub_pos.mpr hl) (sub_pos.mpr hu)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : a ≤ b ∧ b ≤ c)
  (h₁ : a + b + c = 2)
  (h₂ : a * b + b * c + c * a = 1) (node_outer_bounds : 0 ≤ a ∧ c ≤ 4 / 3) (node_smallest_upper_bound : a ≤ 1 / 3) (node_largest_lower_bound : 1 ≤ c) : 0 ≤ a ∧ a ≤ 1 / 3 ∧ 1 / 3 ≤ b ∧ b ≤ 1 ∧ 1 ≤ c ∧ c ≤ 4 / 3 := by
  refine ⟨node_outer_bounds.1, node_smallest_upper_bound, ?_, ?_, node_largest_lower_bound, node_outer_bounds.2⟩
  · linarith [node_outer_bounds.2, h₀.1]
  · linarith [node_outer_bounds.1, h₀.2]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_apbpceq2_abpbcpcaeq1_aleq1on3anbleq1ancleq4on3 (a b c : ℝ)
  (h₀ : a ≤ b ∧ b ≤ c)
  (h₁ : a + b + c = 2)
  (h₂ : a * b + b * c + c * a = 1) : 0 ≤ a ∧ a ≤ 1 / 3 ∧ 1 / 3 ≤ b ∧ b ≤ 1 ∧ 1 ≤ c ∧ c ≤ 4 / 3 := by
  have node_outer_bounds : 0 ≤ a ∧ c ≤ 4 / 3 := by
    have hs := congrArg (fun t : ℝ => t ^ 2) h₁
    have ha := congrArg (fun t : ℝ => a * t) h₁
    have hc := congrArg (fun t : ℝ => c * t) h₁
    constructor
    · nlinarith [sq_nonneg (b - c), sq_nonneg a]
    · nlinarith [sq_nonneg (a - b), sq_nonneg (c - 4 / 3)]
  have node_smallest_upper_bound : a ≤ 1 / 3 := by
    have ha := congrArg (fun t : ℝ => a * t) h₁
    have hprod := mul_nonneg (sub_nonneg.mpr h₀.1) (sub_nonneg.mpr (le_trans h₀.1 h₀.2))
    by_contra hh
    have hl : 1 / 3 < a := lt_of_not_ge hh
    have hu : a < 1 := by linarith [h₀.1, h₀.2]
    have hp := mul_pos (sub_pos.mpr hl) (sub_pos.mpr hu)
    nlinarith
  have node_largest_lower_bound : 1 ≤ c := by
    have hc := congrArg (fun t : ℝ => c * t) h₁
    have hprod := mul_nonneg (sub_nonneg.mpr (le_trans h₀.1 h₀.2)) (sub_nonneg.mpr h₀.2)
    by_contra hh
    have hu : c < 1 := lt_of_not_ge hh
    have hl : 1 / 3 < c := by linarith [h₀.1, h₀.2]
    have hp := mul_pos (sub_pos.mpr hl) (sub_pos.mpr hu)
    nlinarith
  have node_root : 0 ≤ a ∧ a ≤ 1 / 3 ∧ 1 / 3 ≤ b ∧ b ≤ 1 ∧ 1 ≤ c ∧ c ≤ 4 / 3 := by
    refine ⟨node_outer_bounds.1, node_smallest_upper_bound, ?_, ?_, node_largest_lower_bound, node_outer_bounds.2⟩
    · linarith [node_outer_bounds.2, h₀.1]
    · linarith [node_outer_bounds.1, h₀.2]
  exact node_root

#print axioms algebra_apbpceq2_abpbcpcaeq1_aleq1on3anbleq1ancleq4on3
