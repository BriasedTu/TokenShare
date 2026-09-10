import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (r : ℝ) (h₀ : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) = 546) : (743 : ℝ) / 100 ≤ r := by
  by_contra hh
  have hr : r < 743 / 100 := by linarith
  have hb : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) ≤
      ∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k ≤ 57 then (7 : ℤ) else 8) := by
    apply Finset.sum_le_sum
    intro k hk
    have hk91 : (k : ℝ) ≤ 91 := by exact_mod_cast (Finset.mem_Icc.mp hk).2
    by_cases h57 : k ≤ 57
    · rw [if_pos h57]
      have hk57 : (k : ℝ) ≤ 57 := by exact_mod_cast h57
      have hf : Int.floor (r + k / 100) < 8 := Int.floor_lt.mpr (by norm_num; linarith)
      omega
    · rw [if_neg h57]
      have hf : Int.floor (r + k / 100) < 9 := Int.floor_lt.mpr (by norm_num; linarith)
      omega
  have he : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k ≤ 57 then (7 : ℤ) else 8)) = 545 := by decide
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (r : ℝ) (h₀ : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) = 546) : r < (744 : ℝ) / 100 := by
  by_contra hh
  have hr : 744 / 100 ≤ r := by linarith
  have hb : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k < 56 then (7 : ℤ) else 8)) ≤
      ∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100) := by
    apply Finset.sum_le_sum
    intro k hk
    have hk19 : (19 : ℝ) ≤ k := by exact_mod_cast (Finset.mem_Icc.mp hk).1
    by_cases h56 : k < 56
    · rw [if_pos h56]
      apply Int.le_floor.mpr
      norm_num
      linarith
    · rw [if_neg h56]
      have hk56 : (56 : ℝ) ≤ k := by exact_mod_cast (by omega : 56 ≤ k)
      apply Int.le_floor.mpr
      norm_num
      linarith
  have he : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k < 56 then (7 : ℤ) else 8)) = 547 := by decide
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (r : ℝ) (h₀ : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) = 546) (node_lower_floor_threshold : (743 : ℝ) / 100 ≤ r) (node_upper_floor_threshold : r < (744 : ℝ) / 100) : Int.floor (100 * r) = 743 := by
  apply Int.floor_eq_iff.mpr
  constructor <;> norm_num <;> linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1991_p6 (r : ℝ) (h₀ : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) = 546) : Int.floor (100 * r) = 743 := by
  have node_lower_floor_threshold : (743 : ℝ) / 100 ≤ r := by
    by_contra hh
    have hr : r < 743 / 100 := by linarith
    have hb : (∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100)) ≤
        ∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k ≤ 57 then (7 : ℤ) else 8) := by
      apply Finset.sum_le_sum
      intro k hk
      have hk91 : (k : ℝ) ≤ 91 := by exact_mod_cast (Finset.mem_Icc.mp hk).2
      by_cases h57 : k ≤ 57
      · rw [if_pos h57]
        have hk57 : (k : ℝ) ≤ 57 := by exact_mod_cast h57
        have hf : Int.floor (r + k / 100) < 8 := Int.floor_lt.mpr (by norm_num; linarith)
        omega
      · rw [if_neg h57]
        have hf : Int.floor (r + k / 100) < 9 := Int.floor_lt.mpr (by norm_num; linarith)
        omega
    have he : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k ≤ 57 then (7 : ℤ) else 8)) = 545 := by decide
    omega
  have node_upper_floor_threshold : r < (744 : ℝ) / 100 := by
    by_contra hh
    have hr : 744 / 100 ≤ r := by linarith
    have hb : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k < 56 then (7 : ℤ) else 8)) ≤
        ∑ k ∈ Finset.Icc (19 : ℕ) 91, Int.floor (r + k / 100) := by
      apply Finset.sum_le_sum
      intro k hk
      have hk19 : (19 : ℝ) ≤ k := by exact_mod_cast (Finset.mem_Icc.mp hk).1
      by_cases h56 : k < 56
      · rw [if_pos h56]
        apply Int.le_floor.mpr
        norm_num
        linarith
      · rw [if_neg h56]
        have hk56 : (56 : ℝ) ≤ k := by exact_mod_cast (by omega : 56 ≤ k)
        apply Int.le_floor.mpr
        norm_num
        linarith
    have he : (∑ k ∈ Finset.Icc (19 : ℕ) 91, (if k < 56 then (7 : ℤ) else 8)) = 547 := by decide
    omega
  have node_root : Int.floor (100 * r) = 743 := by
    apply Int.floor_eq_iff.mpr
    constructor <;> norm_num <;> linarith
  exact node_root

#print axioms aime_1991_p6
