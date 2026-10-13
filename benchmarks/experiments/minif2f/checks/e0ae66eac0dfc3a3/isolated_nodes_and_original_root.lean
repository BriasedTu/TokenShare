import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ a b : ℝ, 1 < b → b ≤ a → Real.logb a (a / b) + Real.logb b (b / a) ≤ 0 := by
  intro a b hb hba
  have hbp : 0 < b := by linarith
  have hap : 0 < a := by linarith
  have hlb : 0 < Real.log b := Real.log_pos hb
  have hla : 0 < Real.log a := Real.log_pos (by linarith)
  simp only [Real.logb, Real.log_div hap.ne' hbp.ne', Real.log_div hbp.ne' hap.ne']
  calc
    (Real.log a - Real.log b) / Real.log a + (Real.log b - Real.log a) / Real.log b =
        -((Real.log a - Real.log b) ^ 2) / (Real.log a * Real.log b) := by
          field_simp
          ; ring
    _ ≤ 0 := div_nonpos_of_nonpos_of_nonneg (neg_nonpos.mpr (sq_nonneg _)) (le_of_lt (mul_pos hla hlb))

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_logarithmic_upper_bound : ∀ a b : ℝ, 1 < b → b ≤ a → Real.logb a (a / b) + Real.logb b (b / a) ≤ 0) : IsGreatest { y : ℝ | ∃ a b : ℝ, 1 < b ∧ b ≤ a ∧ y = Real.logb a (a / b) + Real.logb b (b / a) }
    0 := by
  constructor
  · refine ⟨2, 2, by norm_num, le_rfl, ?_⟩
    norm_num [Real.logb]
  · rintro y ⟨a, b, hb, hba, rfl⟩
    exact node_logarithmic_upper_bound a b hb hba

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2003_p24 : IsGreatest { y : ℝ | ∃ a b : ℝ, 1 < b ∧ b ≤ a ∧ y = Real.logb a (a / b) + Real.logb b (b / a) }
    0 := by
  have node_logarithmic_upper_bound : ∀ a b : ℝ, 1 < b → b ≤ a → Real.logb a (a / b) + Real.logb b (b / a) ≤ 0 := by
    intro a b hb hba
    have hbp : 0 < b := by linarith
    have hap : 0 < a := by linarith
    have hlb : 0 < Real.log b := Real.log_pos hb
    have hla : 0 < Real.log a := Real.log_pos (by linarith)
    simp only [Real.logb, Real.log_div hap.ne' hbp.ne', Real.log_div hbp.ne' hap.ne']
    calc
      (Real.log a - Real.log b) / Real.log a + (Real.log b - Real.log a) / Real.log b =
          -((Real.log a - Real.log b) ^ 2) / (Real.log a * Real.log b) := by
            field_simp
            ; ring
      _ ≤ 0 := div_nonpos_of_nonpos_of_nonneg (neg_nonpos.mpr (sq_nonneg _)) (le_of_lt (mul_pos hla hlb))
  have node_root : IsGreatest { y : ℝ | ∃ a b : ℝ, 1 < b ∧ b ≤ a ∧ y = Real.logb a (a / b) + Real.logb b (b / a) }
    0 := by
    constructor
    · refine ⟨2, 2, by norm_num, le_rfl, ?_⟩
      norm_num [Real.logb]
    · rintro y ⟨a, b, hb, hba, rfl⟩
      exact node_logarithmic_upper_bound a b hb hba
  exact node_root

#print axioms amc12a_2003_p24
