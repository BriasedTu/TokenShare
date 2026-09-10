import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal) (n : ℕ)
  (h₀ : Finset.prod (Finset.range n) a = 1) : ∀ i ∈ Finset.range n, 0 < (a i : ℝ) := by
  have hp : Finset.prod (Finset.range n) a ≠ 0 := by rw [h₀]; norm_num
  intro i hi
  have ha : a i ≠ 0 := (Finset.prod_ne_zero_iff.mp hp) i hi
  exact_mod_cast (pos_iff_ne_zero.mpr ha : 0 < a i)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal) (n : ℕ)
  (h₀ : Finset.prod (Finset.range n) a = 1) (node_positive_factors : ∀ i ∈ Finset.range n, 0 < (a i : ℝ)) : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) = 0 := by
  rw [← Real.log_prod (Finset.range n) (fun i => (a i : ℝ)) (fun i hi => (node_positive_factors i hi).ne')]
  have hp : (∏ i ∈ Finset.range n, (a i : ℝ)) = 1 := by exact_mod_cast h₀
  rw [hp, Real.log_one]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal) (n : ℕ)
  (h₀ : Finset.prod (Finset.range n) a = 1) (node_positive_factors : ∀ i ∈ Finset.range n, 0 < (a i : ℝ)) : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) ≤ (∑ i ∈ Finset.range n, (a i : ℝ)) - n := by
  calc
    (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) ≤ ∑ i ∈ Finset.range n, ((a i : ℝ) - 1) :=
      Finset.sum_le_sum (fun i hi => Real.log_le_sub_one_of_pos (node_positive_factors i hi))
    _ = (∑ i ∈ Finset.range n, (a i : ℝ)) - n := by simp [Finset.sum_sub_distrib]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal) (n : ℕ)
  (h₀ : Finset.prod (Finset.range n) a = 1) (node_logarithmic_conservation : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) = 0) (node_summed_tangent_bound : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) ≤ (∑ i ∈ Finset.range n, (a i : ℝ)) - n) : Finset.sum (Finset.range n) a ≥ n := by
  have hs : (n : ℝ) ≤ ∑ i ∈ Finset.range n, (a i : ℝ) := by linarith
  exact_mod_cast hs

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_amgm_prod1toneq1_sum1tongeqn (a : ℕ → NNReal) (n : ℕ)
  (h₀ : Finset.prod (Finset.range n) a = 1) : Finset.sum (Finset.range n) a ≥ n := by
  have node_positive_factors : ∀ i ∈ Finset.range n, 0 < (a i : ℝ) := by
    have hp : Finset.prod (Finset.range n) a ≠ 0 := by rw [h₀]; norm_num
    intro i hi
    have ha : a i ≠ 0 := (Finset.prod_ne_zero_iff.mp hp) i hi
    exact_mod_cast (pos_iff_ne_zero.mpr ha : 0 < a i)
  have node_logarithmic_conservation : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) = 0 := by
    rw [← Real.log_prod (Finset.range n) (fun i => (a i : ℝ)) (fun i hi => (node_positive_factors i hi).ne')]
    have hp : (∏ i ∈ Finset.range n, (a i : ℝ)) = 1 := by exact_mod_cast h₀
    rw [hp, Real.log_one]
  have node_summed_tangent_bound : (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) ≤ (∑ i ∈ Finset.range n, (a i : ℝ)) - n := by
    calc
      (∑ i ∈ Finset.range n, Real.log (a i : ℝ)) ≤ ∑ i ∈ Finset.range n, ((a i : ℝ) - 1) :=
        Finset.sum_le_sum (fun i hi => Real.log_le_sub_one_of_pos (node_positive_factors i hi))
      _ = (∑ i ∈ Finset.range n, (a i : ℝ)) - n := by simp [Finset.sum_sub_distrib]
  have node_root : Finset.sum (Finset.range n) a ≥ n := by
    have hs : (n : ℝ) ≤ ∑ i ∈ Finset.range n, (a i : ℝ) := by linarith
    exact_mod_cast hs
  exact node_root

#print axioms algebra_amgm_prod1toneq1_sum1tongeqn
