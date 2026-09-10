import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : Real.sqrt (Real.log 3 / Real.log 2) * Real.sqrt (Real.log 2 / Real.log 3) = 1 := by
  have h2 : 0 < Real.log 2 := Real.log_pos (by norm_num)
  have h3 : 0 < Real.log 3 := Real.log_pos (by norm_num)
  rw [← Real.sqrt_mul (div_nonneg h3.le h2.le)]
  have he : Real.log 3 / Real.log 2 * (Real.log 2 / Real.log 3)=1 := by field_simp
  rw [he]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_reciprocal_radical_product : Real.sqrt (Real.log 3 / Real.log 2) * Real.sqrt (Real.log 2 / Real.log 3) = 1) : Real.sqrt (Real.log 6 / Real.log 2 + Real.log 6 / Real.log 3) = Real.sqrt (Real.log 3 / Real.log 2) + Real.sqrt (Real.log 2 / Real.log 3) := by
  have h2 : 0 < Real.log 2 := Real.log_pos (by norm_num)
  have h3 : 0 < Real.log 3 := Real.log_pos (by norm_num)
  have h6 : Real.log 6=Real.log 2+Real.log 3 := by rw [show (6:ℝ)=2*3 by norm_num, Real.log_mul (by norm_num) (by norm_num)]
  have hs2 := Real.sq_sqrt (div_nonneg h3.le h2.le)
  have hs3 := Real.sq_sqrt (div_nonneg h2.le h3.le)
  have he : Real.log 6 / Real.log 2 + Real.log 6 / Real.log 3=Real.log 3/Real.log 2+Real.log 2/Real.log 3+2 := by rw [h6]; field_simp; ring
  apply (Real.sqrt_eq_iff_eq_sq (by rw [he]; positivity) (by positivity)).2
  rw [he]
  nlinarith only [hs2, hs3, node_reciprocal_radical_product]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12b_2020_p13 : Real.sqrt (Real.log 6 / Real.log 2 + Real.log 6 / Real.log 3) = Real.sqrt (Real.log 3 / Real.log 2) + Real.sqrt (Real.log 2 / Real.log 3) := by
  have node_reciprocal_radical_product : Real.sqrt (Real.log 3 / Real.log 2) * Real.sqrt (Real.log 2 / Real.log 3) = 1 := by
    have h2 : 0 < Real.log 2 := Real.log_pos (by norm_num)
    have h3 : 0 < Real.log 3 := Real.log_pos (by norm_num)
    rw [← Real.sqrt_mul (div_nonneg h3.le h2.le)]
    have he : Real.log 3 / Real.log 2 * (Real.log 2 / Real.log 3)=1 := by field_simp
    rw [he]
    norm_num
  have node_root : Real.sqrt (Real.log 6 / Real.log 2 + Real.log 6 / Real.log 3) = Real.sqrt (Real.log 3 / Real.log 2) + Real.sqrt (Real.log 2 / Real.log 3) := by
    have h2 : 0 < Real.log 2 := Real.log_pos (by norm_num)
    have h3 : 0 < Real.log 3 := Real.log_pos (by norm_num)
    have h6 : Real.log 6=Real.log 2+Real.log 3 := by rw [show (6:ℝ)=2*3 by norm_num, Real.log_mul (by norm_num) (by norm_num)]
    have hs2 := Real.sq_sqrt (div_nonneg h3.le h2.le)
    have hs3 := Real.sq_sqrt (div_nonneg h2.le h3.le)
    have he : Real.log 6 / Real.log 2 + Real.log 6 / Real.log 3=Real.log 3/Real.log 2+Real.log 2/Real.log 3+2 := by rw [h6]; field_simp; ring
    apply (Real.sqrt_eq_iff_eq_sq (by rw [he]; positivity) (by positivity)).2
    rw [he]
    nlinarith only [hs2, hs3, node_reciprocal_radical_product]
  exact node_root

#print axioms amc12b_2020_p13
