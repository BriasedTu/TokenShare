import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ((Real.sqrt 2)^(Real.sqrt 2):ℝ)^(Real.sqrt 2) = 2 := by
  rw [← Real.rpow_mul (Real.sqrt_nonneg 2), Real.mul_self_sqrt (by norm_num : (0:ℝ) ≤ 2)]
  norm_cast
  exact Real.sq_sqrt (by norm_num)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_iterated_irrational_power : ((Real.sqrt 2)^(Real.sqrt 2):ℝ)^(Real.sqrt 2) = 2) : ∃ a b, Irrational a ∧ Irrational b ∧ ¬ Irrational (a^b) := by
  by_cases h : Irrational ((Real.sqrt 2)^(Real.sqrt 2):ℝ)
  · refine ⟨(Real.sqrt 2)^(Real.sqrt 2), Real.sqrt 2, h, irrational_sqrt_two, ?_⟩
    rw [node_iterated_irrational_power]
    exact fun hi => hi ⟨2, by norm_num⟩
  · exact ⟨Real.sqrt 2, Real.sqrt 2, irrational_sqrt_two, irrational_sqrt_two, h⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_others_exirrpowirrrat : ∃ a b, Irrational a ∧ Irrational b ∧ ¬ Irrational (a^b) := by
  have node_iterated_irrational_power : ((Real.sqrt 2)^(Real.sqrt 2):ℝ)^(Real.sqrt 2) = 2 := by
    rw [← Real.rpow_mul (Real.sqrt_nonneg 2), Real.mul_self_sqrt (by norm_num : (0:ℝ) ≤ 2)]
    norm_cast
    exact Real.sq_sqrt (by norm_num)
  have node_root : ∃ a b, Irrational a ∧ Irrational b ∧ ¬ Irrational (a^b) := by
    by_cases h : Irrational ((Real.sqrt 2)^(Real.sqrt 2):ℝ)
    · refine ⟨(Real.sqrt 2)^(Real.sqrt 2), Real.sqrt 2, h, irrational_sqrt_two, ?_⟩
      rw [node_iterated_irrational_power]
      exact fun hi => hi ⟨2, by norm_num⟩
    · exact ⟨Real.sqrt 2, Real.sqrt 2, irrational_sqrt_two, irrational_sqrt_two, h⟩
  exact node_root

#print axioms algebra_others_exirrpowirrrat
