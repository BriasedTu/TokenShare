import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal)
  (n : ℕ)
  (h₀ : ∑ x ∈ Finset.range n, a x = n) : ∑ i ∈ Finset.range n, Real.sqrt (a i) ≤ n := by
  have hs : (∑ i ∈ Finset.range n, (a i:ℝ)) = n := by exact_mod_cast h₀
  have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.range n) (r := fun i => Real.sqrt (a i)) (f := fun _ => (1:ℝ)) (g := fun i => (a i:ℝ)) (by intros; norm_num) (by intros; positivity) (by intros; simp)
  simp only [Finset.sum_const, Finset.card_range, nsmul_eq_mul, mul_one, hs] at h
  have hp : 0 ≤ ∑ i ∈ Finset.range n, Real.sqrt (a i) := Finset.sum_nonneg (by intros; positivity)
  nlinarith [Nat.cast_nonneg (α := ℝ) n]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → NNReal)
  (n : ℕ)
  (h₀ : ∑ x ∈ Finset.range n, a x = n) (node_square_root_sum_bound : ∑ i ∈ Finset.range n, Real.sqrt (a i) ≤ n) : ∏ x ∈ Finset.range n, a x ≤ 1 := by
  by_cases hz : ∃ i ∈ Finset.range n, a i=0
  · have hp := Finset.prod_eq_zero_iff.2 hz
    rw [hp]
    exact zero_le_one
  · have hp : ∀ i ∈ Finset.range n, 0<a i := by intro i hi; exact pos_iff_ne_zero.mpr (by intro he; exact hz ⟨i,hi,he⟩)
    have ht : ∀ i ∈ Finset.range n, Real.log (a i) ≤ 2*(Real.sqrt (a i)-1) := by
      intro i hi
      have hi0 : (0:ℝ)<a i := by exact_mod_cast hp i hi
      have hr := Real.sqrt_pos.2 hi0
      have hl := Real.log_le_sub_one_of_pos hr
      have he : Real.log (a i)=2*Real.log (Real.sqrt (a i)) := by
        conv_lhs => rw [← Real.mul_self_sqrt hi0.le]
        rw [Real.log_mul hr.ne' hr.ne']
        ring
      linarith
    have hsum := Finset.sum_le_sum ht
    have hs : ∑ i ∈ Finset.range n, Real.log (a i) ≤ 0 := by
      rw [← Finset.mul_sum, Finset.sum_sub_distrib] at hsum
      simp only [Finset.sum_const, Finset.card_range, nsmul_eq_mul, mul_one] at hsum
      nlinarith only [hsum,node_square_root_sum_bound]
    have he : Real.log (∏ i ∈ Finset.range n, (a i:ℝ)) = ∑ i ∈ Finset.range n, Real.log (a i) := Real.log_prod _ _ (by intro i hi; exact (show (0:ℝ)<a i by exact_mod_cast hp i hi).ne')
    have hb : (∏ i ∈ Finset.range n, (a i:ℝ)) ≤ 1 := (Real.log_nonpos_iff (by positivity)).1 (he ▸ hs)
    exact_mod_cast hb

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_amgm_sum1toneqn_prod1tonleq1 (a : ℕ → NNReal)
  (n : ℕ)
  (h₀ : ∑ x ∈ Finset.range n, a x = n) : ∏ x ∈ Finset.range n, a x ≤ 1 := by
  have node_square_root_sum_bound : ∑ i ∈ Finset.range n, Real.sqrt (a i) ≤ n := by
    have hs : (∑ i ∈ Finset.range n, (a i:ℝ)) = n := by exact_mod_cast h₀
    have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.range n) (r := fun i => Real.sqrt (a i)) (f := fun _ => (1:ℝ)) (g := fun i => (a i:ℝ)) (by intros; norm_num) (by intros; positivity) (by intros; simp)
    simp only [Finset.sum_const, Finset.card_range, nsmul_eq_mul, mul_one, hs] at h
    have hp : 0 ≤ ∑ i ∈ Finset.range n, Real.sqrt (a i) := Finset.sum_nonneg (by intros; positivity)
    nlinarith [Nat.cast_nonneg (α := ℝ) n]
  have node_root : ∏ x ∈ Finset.range n, a x ≤ 1 := by
    by_cases hz : ∃ i ∈ Finset.range n, a i=0
    · have hp := Finset.prod_eq_zero_iff.2 hz
      rw [hp]
      exact zero_le_one
    · have hp : ∀ i ∈ Finset.range n, 0<a i := by intro i hi; exact pos_iff_ne_zero.mpr (by intro he; exact hz ⟨i,hi,he⟩)
      have ht : ∀ i ∈ Finset.range n, Real.log (a i) ≤ 2*(Real.sqrt (a i)-1) := by
        intro i hi
        have hi0 : (0:ℝ)<a i := by exact_mod_cast hp i hi
        have hr := Real.sqrt_pos.2 hi0
        have hl := Real.log_le_sub_one_of_pos hr
        have he : Real.log (a i)=2*Real.log (Real.sqrt (a i)) := by
          conv_lhs => rw [← Real.mul_self_sqrt hi0.le]
          rw [Real.log_mul hr.ne' hr.ne']
          ring
        linarith
      have hsum := Finset.sum_le_sum ht
      have hs : ∑ i ∈ Finset.range n, Real.log (a i) ≤ 0 := by
        rw [← Finset.mul_sum, Finset.sum_sub_distrib] at hsum
        simp only [Finset.sum_const, Finset.card_range, nsmul_eq_mul, mul_one] at hsum
        nlinarith only [hsum,node_square_root_sum_bound]
      have he : Real.log (∏ i ∈ Finset.range n, (a i:ℝ)) = ∑ i ∈ Finset.range n, Real.log (a i) := Real.log_prod _ _ (by intro i hi; exact (show (0:ℝ)<a i by exact_mod_cast hp i hi).ne')
      have hb : (∏ i ∈ Finset.range n, (a i:ℝ)) ≤ 1 := (Real.log_nonpos_iff (by positivity)).1 (he ▸ hs)
      exact_mod_cast hb
  exact node_root

#print axioms algebra_amgm_sum1toneqn_prod1tonleq1
