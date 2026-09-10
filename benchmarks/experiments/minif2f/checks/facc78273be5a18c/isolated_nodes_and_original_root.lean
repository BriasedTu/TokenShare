import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ k : ℕ, 2 ≤ k → 1 / Real.sqrt k < 2*(Real.sqrt k-Real.sqrt (k-1)) := by
  intro k hk
  have hk0 : 0 < (k:ℝ) := by exact_mod_cast (by omega : 0 < k)
  have ht : (k-1:ℕ) < k := by omega
  have hlt : Real.sqrt (k-1:ℕ) < Real.sqrt k := Real.sqrt_lt_sqrt (by positivity) (by exact_mod_cast ht)
  have hs := Real.sq_sqrt hk0.le
  have hs' := Real.sq_sqrt (by positivity : 0 ≤ ((k-1:ℕ):ℝ))
  have hc : ((k-1:ℕ):ℝ)=(k:ℝ)-1 := by rw [Nat.cast_sub (by omega : 1 ≤ k), Nat.cast_one]
  rw [hc] at hs'
  apply (div_lt_iff₀ (Real.sqrt_pos.2 hk0)).2
  have hp := sq_pos_of_pos (sub_pos.mpr hlt)
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ n : ℕ, 1 ≤ n → (∑ k ∈ Finset.Icc 2 n, 2*(Real.sqrt k-Real.sqrt (k-1))) = 2*(Real.sqrt n-1) := by
  intro n hn
  induction n with
  | zero => omega
  | succ n ih =>
    by_cases hz : n=0
    · subst n; norm_num
    · rw [Finset.sum_Icc_succ_top (by omega : 2 ≤ n+1), ih (by omega)]
      simp only [Nat.cast_add, Nat.cast_one, add_sub_cancel_right]
      ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_pointwise_telescope_bound : ∀ k : ℕ, 2 ≤ k → 1 / Real.sqrt k < 2*(Real.sqrt k-Real.sqrt (k-1))) (node_telescoping_sum : ∀ n : ℕ, 1 ≤ n → (∑ k ∈ Finset.Icc 2 n, 2*(Real.sqrt k-Real.sqrt (k-1))) = 2*(Real.sqrt n-1)) : ∑ k ∈ (Finset.Icc (2 : ℕ) 10000), (1 / Real.sqrt k) < 198 := by
  have hs := Finset.sum_lt_sum (fun k (hk : k ∈ Finset.Icc (2:ℕ) 10000) => (node_pointwise_telescope_bound k (Finset.mem_Icc.mp hk).1).le) (show ∃ k ∈ Finset.Icc (2:ℕ) 10000, 1/Real.sqrt k < 2*(Real.sqrt k-Real.sqrt (k-1)) from ⟨2, by norm_num, node_pointwise_telescope_bound 2 (by norm_num)⟩)
  have he := node_telescoping_sum 10000 (by norm_num)
  norm_num at he
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_sum1onsqrt2to1onsqrt10000lt198 : ∑ k ∈ (Finset.Icc (2 : ℕ) 10000), (1 / Real.sqrt k) < 198 := by
  have node_pointwise_telescope_bound : ∀ k : ℕ, 2 ≤ k → 1 / Real.sqrt k < 2*(Real.sqrt k-Real.sqrt (k-1)) := by
    intro k hk
    have hk0 : 0 < (k:ℝ) := by exact_mod_cast (by omega : 0 < k)
    have ht : (k-1:ℕ) < k := by omega
    have hlt : Real.sqrt (k-1:ℕ) < Real.sqrt k := Real.sqrt_lt_sqrt (by positivity) (by exact_mod_cast ht)
    have hs := Real.sq_sqrt hk0.le
    have hs' := Real.sq_sqrt (by positivity : 0 ≤ ((k-1:ℕ):ℝ))
    have hc : ((k-1:ℕ):ℝ)=(k:ℝ)-1 := by rw [Nat.cast_sub (by omega : 1 ≤ k), Nat.cast_one]
    rw [hc] at hs'
    apply (div_lt_iff₀ (Real.sqrt_pos.2 hk0)).2
    have hp := sq_pos_of_pos (sub_pos.mpr hlt)
    nlinarith
  have node_telescoping_sum : ∀ n : ℕ, 1 ≤ n → (∑ k ∈ Finset.Icc 2 n, 2*(Real.sqrt k-Real.sqrt (k-1))) = 2*(Real.sqrt n-1) := by
    intro n hn
    induction n with
    | zero => omega
    | succ n ih =>
      by_cases hz : n=0
      · subst n; norm_num
      · rw [Finset.sum_Icc_succ_top (by omega : 2 ≤ n+1), ih (by omega)]
        simp only [Nat.cast_add, Nat.cast_one, add_sub_cancel_right]
        ring
  have node_root : ∑ k ∈ (Finset.Icc (2 : ℕ) 10000), (1 / Real.sqrt k) < 198 := by
    have hs := Finset.sum_lt_sum (fun k (hk : k ∈ Finset.Icc (2:ℕ) 10000) => (node_pointwise_telescope_bound k (Finset.mem_Icc.mp hk).1).le) (show ∃ k ∈ Finset.Icc (2:ℕ) 10000, 1/Real.sqrt k < 2*(Real.sqrt k-Real.sqrt (k-1)) from ⟨2, by norm_num, node_pointwise_telescope_bound 2 (by norm_num)⟩)
    have he := node_telescoping_sum 10000 (by norm_num)
    norm_num at he
    linarith
  exact node_root

#print axioms algebra_sum1onsqrt2to1onsqrt10000lt198
