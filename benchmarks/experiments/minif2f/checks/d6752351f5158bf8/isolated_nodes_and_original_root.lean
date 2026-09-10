import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ) (h₀ : 0 < n)
  (h₀ : (∑ k ∈ Finset.Icc 1 n, Int.floor (Real.logb 2 k)) = 1994) : ∀ m : ℕ, (∑ k ∈ Finset.Icc 1 m, Nat.log 2 k) = 1994 → m = 312 := by
  intro m hm
  have h311 : (∑ k ∈ Finset.Icc 1 311, Nat.log 2 k) = 1986 := by
    set_option maxRecDepth 10000 in norm_num [Finset.sum_Icc_succ_top]
  have h313 : (∑ k ∈ Finset.Icc 1 313, Nat.log 2 k) = 2002 := by
    set_option maxRecDepth 10000 in norm_num [Finset.sum_Icc_succ_top]
  by_contra he
  rcases lt_or_gt_of_ne he with hl | hg
  · have hsub : Finset.Icc 1 m ⊆ Finset.Icc 1 311 := Finset.Icc_subset_Icc le_rfl (by omega)
    have hb := Finset.sum_le_sum_of_subset_of_nonneg hsub (fun i hi hni => Nat.zero_le (Nat.log 2 i))
    omega
  · have hsub : Finset.Icc 1 313 ⊆ Finset.Icc 1 m := Finset.Icc_subset_Icc le_rfl (by omega)
    have hb := Finset.sum_le_sum_of_subset_of_nonneg hsub (fun i hi hni => Nat.zero_le (Nat.log 2 i))
    omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ) (h₀ : 0 < n)
  (h₀ : (∑ k ∈ Finset.Icc 1 n, Int.floor (Real.logb 2 k)) = 1994) (node_discrete_threshold : ∀ m : ℕ, (∑ k ∈ Finset.Icc 1 m, Nat.log 2 k) = 1994 → m = 312) : n = 312 := by
  have ht : (∑ k ∈ Finset.Icc 1 n, Int.floor (Real.logb 2 k)) = ((∑ k ∈ Finset.Icc 1 n, Nat.log 2 k : ℕ) : ℤ) := by
    push_cast
    apply Finset.sum_congr rfl
    intro k hk
    simpa only [Int.log_natCast] using (Real.floor_logb_natCast (b := 2) (r := (k : ℝ)) (Nat.cast_nonneg k))
  rw [h₀] at ht
  have hn : (∑ k ∈ Finset.Icc 1 n, Nat.log 2 k) = 1994 := by exact_mod_cast ht.symm
  exact node_discrete_threshold n hn

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1994_p4 (n : ℕ) (h₀ : 0 < n)
  (h₀ : (∑ k ∈ Finset.Icc 1 n, Int.floor (Real.logb 2 k)) = 1994) : n = 312 := by
  have node_discrete_threshold : ∀ m : ℕ, (∑ k ∈ Finset.Icc 1 m, Nat.log 2 k) = 1994 → m = 312 := by
    intro m hm
    have h311 : (∑ k ∈ Finset.Icc 1 311, Nat.log 2 k) = 1986 := by
      set_option maxRecDepth 10000 in norm_num [Finset.sum_Icc_succ_top]
    have h313 : (∑ k ∈ Finset.Icc 1 313, Nat.log 2 k) = 2002 := by
      set_option maxRecDepth 10000 in norm_num [Finset.sum_Icc_succ_top]
    by_contra he
    rcases lt_or_gt_of_ne he with hl | hg
    · have hsub : Finset.Icc 1 m ⊆ Finset.Icc 1 311 := Finset.Icc_subset_Icc le_rfl (by omega)
      have hb := Finset.sum_le_sum_of_subset_of_nonneg hsub (fun i hi hni => Nat.zero_le (Nat.log 2 i))
      omega
    · have hsub : Finset.Icc 1 313 ⊆ Finset.Icc 1 m := Finset.Icc_subset_Icc le_rfl (by omega)
      have hb := Finset.sum_le_sum_of_subset_of_nonneg hsub (fun i hi hni => Nat.zero_le (Nat.log 2 i))
      omega
  have node_root : n = 312 := by
    have ht : (∑ k ∈ Finset.Icc 1 n, Int.floor (Real.logb 2 k)) = ((∑ k ∈ Finset.Icc 1 n, Nat.log 2 k : ℕ) : ℤ) := by
      push_cast
      apply Finset.sum_congr rfl
      intro k hk
      simpa only [Int.log_natCast] using (Real.floor_logb_natCast (b := 2) (r := (k : ℝ)) (Nat.cast_nonneg k))
    rw [h₀] at ht
    have hn : (∑ k ∈ Finset.Icc 1 n, Nat.log 2 k) = 1994 := by exact_mod_cast ht.symm
    exact node_discrete_threshold n hn
  exact node_root

#print axioms aime_1994_p4
