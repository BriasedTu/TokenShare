import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
    (h₀ :
      x =
        (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) /
          ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) : (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) = Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) - Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) := by
  have hr : (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) =
      ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (π / 4 - n * π / 180) := by
    refine Finset.sum_bij (fun n hn => 45 - n) ?_ ?_ ?_ ?_
    · intro n hn
      have hn' := Finset.mem_Icc.mp hn
      apply Finset.mem_Icc.mpr
      dsimp only
      omega
    · intro n hn m hm he
      dsimp only at he
      have hn' := Finset.mem_Icc.mp hn
      have hm' := Finset.mem_Icc.mp hm
      omega
    · intro m hm
      have hm' := Finset.mem_Icc.mp hm
      refine ⟨45 - m, Finset.mem_Icc.mpr (by omega), ?_⟩
      dsimp only
      omega
    · intro n hn
      have hn' := Finset.mem_Icc.mp hn
      congr 1
      rw [Nat.cast_sub (by omega : n ≤ 45)]
      push_cast
      ring
  simp_rw [Real.sin_sub, Real.sin_pi_div_four, Real.cos_pi_div_four] at hr
  rw [Finset.sum_sub_distrib, ← Finset.mul_sum, ← Finset.mul_sum] at hr
  exact hr

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
    (h₀ :
      x =
        (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) /
          ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) : 0 < (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) := by
  apply Finset.sum_pos
  · intro n hn
    have hb := Finset.mem_Icc.mp hn
    have hn0 : (0 : ℝ) < n := by exact_mod_cast (by omega : 0 < n)
    have hn44 : (n : ℝ) ≤ 44 := by exact_mod_cast hb.2
    apply Real.sin_pos_of_pos_of_lt_pi
    · positivity
    · nlinarith [mul_nonneg (show 0 ≤ 44 - (n : ℝ) by linarith) Real.pi_pos.le, Real.pi_pos]
  · exact ⟨1, by simp⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
    (h₀ :
      x =
        (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) /
          ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) (node_complementary_angle_sum : (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) = Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) - Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180))) (node_positive_sine_sum : 0 < (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180))) : Int.floor (100 * x) = 241 := by
  let s : ℝ := ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)
  let c : ℝ := ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)
  have hr : s = Real.sqrt 2 / 2 * c - Real.sqrt 2 / 2 * s := node_complementary_angle_sum
  have hs : (Real.sqrt 2) ^ 2 = (2 : ℝ) := Real.sq_sqrt (by norm_num)
  have hh := congrArg (fun z : ℝ => z * (2 * Real.sqrt 2)) hr
  dsimp only at hh
  ring_nf at hh
  norm_num [hs] at hh
  have hc : c = (1 + Real.sqrt 2) * s := by nlinarith
  have hx : x = 1 + Real.sqrt 2 := by
    rw [h₀]
    change c / s = 1 + Real.sqrt 2
    rw [hc]
    exact mul_div_cancel_right₀ _ node_positive_sine_sum.ne'
  rw [hx]
  apply Int.floor_eq_iff.mpr
  constructor <;> norm_num <;> nlinarith [hs, Real.sqrt_nonneg 2]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1997_p11 (x : ℝ)
    (h₀ :
      x =
        (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) /
          ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) : Int.floor (100 * x) = 241 := by
  have node_complementary_angle_sum : (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) = Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)) - Real.sqrt 2 / 2 * (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) := by
    have hr : (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) =
        ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (π / 4 - n * π / 180) := by
      refine Finset.sum_bij (fun n hn => 45 - n) ?_ ?_ ?_ ?_
      · intro n hn
        have hn' := Finset.mem_Icc.mp hn
        apply Finset.mem_Icc.mpr
        dsimp only
        omega
      · intro n hn m hm he
        dsimp only at he
        have hn' := Finset.mem_Icc.mp hn
        have hm' := Finset.mem_Icc.mp hm
        omega
      · intro m hm
        have hm' := Finset.mem_Icc.mp hm
        refine ⟨45 - m, Finset.mem_Icc.mpr (by omega), ?_⟩
        dsimp only
        omega
      · intro n hn
        have hn' := Finset.mem_Icc.mp hn
        congr 1
        rw [Nat.cast_sub (by omega : n ≤ 45)]
        push_cast
        ring
    simp_rw [Real.sin_sub, Real.sin_pi_div_four, Real.cos_pi_div_four] at hr
    rw [Finset.sum_sub_distrib, ← Finset.mul_sum, ← Finset.mul_sum] at hr
    exact hr
  have node_positive_sine_sum : 0 < (∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)) := by
    apply Finset.sum_pos
    · intro n hn
      have hb := Finset.mem_Icc.mp hn
      have hn0 : (0 : ℝ) < n := by exact_mod_cast (by omega : 0 < n)
      have hn44 : (n : ℝ) ≤ 44 := by exact_mod_cast hb.2
      apply Real.sin_pos_of_pos_of_lt_pi
      · positivity
      · nlinarith [mul_nonneg (show 0 ≤ 44 - (n : ℝ) by linarith) Real.pi_pos.le, Real.pi_pos]
    · exact ⟨1, by simp⟩
  have node_root : Int.floor (100 * x) = 241 := by
    let s : ℝ := ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.sin (n * π / 180)
    let c : ℝ := ∑ n ∈ Finset.Icc (1 : ℕ) 44, Real.cos (n * π / 180)
    have hr : s = Real.sqrt 2 / 2 * c - Real.sqrt 2 / 2 * s := node_complementary_angle_sum
    have hs : (Real.sqrt 2) ^ 2 = (2 : ℝ) := Real.sq_sqrt (by norm_num)
    have hh := congrArg (fun z : ℝ => z * (2 * Real.sqrt 2)) hr
    dsimp only at hh
    ring_nf at hh
    norm_num [hs] at hh
    have hc : c = (1 + Real.sqrt 2) * s := by nlinarith
    have hx : x = 1 + Real.sqrt 2 := by
      rw [h₀]
      change c / s = 1 + Real.sqrt 2
      rw [hc]
      exact mul_div_cancel_right₀ _ node_positive_sine_sum.ne'
    rw [hx]
    apply Int.floor_eq_iff.mpr
    constructor <;> norm_num <;> nlinarith [hs, Real.sqrt_nonneg 2]
  exact node_root

#print axioms aime_1997_p11
