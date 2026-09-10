import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 1 = 1) (h₁ : a 2 = 1 / Real.sqrt 3)
  (h₂ : ∀ n, 1 ≤ n → a (n + 2) = (a n + a (n + 1)) / (1 - a n * a (n + 1))) : a 17 = 0 ∧ a 25 = 1 ∧ a 26 = 1 / Real.sqrt 3 := by
  have hs : (Real.sqrt 3) ^ 2 = (3 : ℝ) := Real.sq_sqrt (by norm_num)
  have hsp : 0 < Real.sqrt 3 := Real.sqrt_pos.mpr (by norm_num)
  have hs3 : (Real.sqrt 3) ^ 3 = 3 * Real.sqrt 3 := by
    calc (Real.sqrt 3) ^ 3 = (Real.sqrt 3) ^ 2 * Real.sqrt 3 := by ring
         _ = 3 * Real.sqrt 3 := by rw [hs]
  have v1 : a 1 = (1 + 0 * Real.sqrt 3) := by simpa using h₀
  have v2 : a 2 = (0 + (1 / 3) * Real.sqrt 3) := by
    rw [h₁]
    field_simp
    nlinarith [hs]
  have v3 : a 3 = (2 + 1 * Real.sqrt 3) := by
    rw [show 3 = 1 + 2 by decide, h₂ 1 (by norm_num), v1, v2]
    have hd : 1 - (1 + 0 * Real.sqrt 3) * (0 + (1 / 3) * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v4 : a 4 = (-2 + -1 * Real.sqrt 3) := by
    rw [show 4 = 2 + 2 by decide, h₂ 2 (by norm_num), v2, v3]
    have hd : 1 - (0 + (1 / 3) * Real.sqrt 3) * (2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v5 : a 5 = (0 + 0 * Real.sqrt 3) := by
    rw [show 5 = 3 + 2 by decide, h₂ 3 (by norm_num), v3, v4]
    have hd : 1 - (2 + 1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v6 : a 6 = (-2 + -1 * Real.sqrt 3) := by
    rw [show 6 = 4 + 2 by decide, h₂ 4 (by norm_num), v4, v5]
    have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v7 : a 7 = (-2 + -1 * Real.sqrt 3) := by
    rw [show 7 = 5 + 2 by decide, h₂ 5 (by norm_num), v5, v6]
    have hd : 1 - (0 + 0 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v8 : a 8 = (0 + (1 / 3) * Real.sqrt 3) := by
    rw [show 8 = 6 + 2 by decide, h₂ 6 (by norm_num), v6, v7]
    have hd : 1 - (-2 + -1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v9 : a 9 = (-1 + 0 * Real.sqrt 3) := by
    rw [show 9 = 7 + 2 by decide, h₂ 7 (by norm_num), v7, v8]
    have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + (1 / 3) * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v10 : a 10 = (-2 + 1 * Real.sqrt 3) := by
    rw [show 10 = 8 + 2 by decide, h₂ 8 (by norm_num), v8, v9]
    have hd : 1 - (0 + (1 / 3) * Real.sqrt 3) * (-1 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v11 : a 11 = (0 + -1 * Real.sqrt 3) := by
    rw [show 11 = 9 + 2 by decide, h₂ 9 (by norm_num), v9, v10]
    have hd : 1 - (-1 + 0 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v12 : a 12 = (-2 + -1 * Real.sqrt 3) := by
    rw [show 12 = 10 + 2 by decide, h₂ 10 (by norm_num), v10, v11]
    have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v13 : a 13 = (1 + 0 * Real.sqrt 3) := by
    rw [show 13 = 11 + 2 by decide, h₂ 11 (by norm_num), v11, v12]
    have hd : 1 - (0 + -1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v14 : a 14 = (0 + (-1 / 3) * Real.sqrt 3) := by
    rw [show 14 = 12 + 2 by decide, h₂ 12 (by norm_num), v12, v13]
    have hd : 1 - (-2 + -1 * Real.sqrt 3) * (1 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v15 : a 15 = (2 + -1 * Real.sqrt 3) := by
    rw [show 15 = 13 + 2 by decide, h₂ 13 (by norm_num), v13, v14]
    have hd : 1 - (1 + 0 * Real.sqrt 3) * (0 + (-1 / 3) * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v16 : a 16 = (-2 + 1 * Real.sqrt 3) := by
    rw [show 16 = 14 + 2 by decide, h₂ 14 (by norm_num), v14, v15]
    have hd : 1 - (0 + (-1 / 3) * Real.sqrt 3) * (2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v17 : a 17 = (0 + 0 * Real.sqrt 3) := by
    rw [show 17 = 15 + 2 by decide, h₂ 15 (by norm_num), v15, v16]
    have hd : 1 - (2 + -1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v18 : a 18 = (-2 + 1 * Real.sqrt 3) := by
    rw [show 18 = 16 + 2 by decide, h₂ 16 (by norm_num), v16, v17]
    have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v19 : a 19 = (-2 + 1 * Real.sqrt 3) := by
    rw [show 19 = 17 + 2 by decide, h₂ 17 (by norm_num), v17, v18]
    have hd : 1 - (0 + 0 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v20 : a 20 = (0 + (-1 / 3) * Real.sqrt 3) := by
    rw [show 20 = 18 + 2 by decide, h₂ 18 (by norm_num), v18, v19]
    have hd : 1 - (-2 + 1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v21 : a 21 = (-1 + 0 * Real.sqrt 3) := by
    rw [show 21 = 19 + 2 by decide, h₂ 19 (by norm_num), v19, v20]
    have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + (-1 / 3) * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v22 : a 22 = (-2 + -1 * Real.sqrt 3) := by
    rw [show 22 = 20 + 2 by decide, h₂ 20 (by norm_num), v20, v21]
    have hd : 1 - (0 + (-1 / 3) * Real.sqrt 3) * (-1 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v23 : a 23 = (0 + 1 * Real.sqrt 3) := by
    rw [show 23 = 21 + 2 by decide, h₂ 21 (by norm_num), v21, v22]
    have hd : 1 - (-1 + 0 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v24 : a 24 = (-2 + 1 * Real.sqrt 3) := by
    rw [show 24 = 22 + 2 by decide, h₂ 22 (by norm_num), v22, v23]
    have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v25 : a 25 = (1 + 0 * Real.sqrt 3) := by
    rw [show 25 = 23 + 2 by decide, h₂ 23 (by norm_num), v23, v24]
    have hd : 1 - (0 + 1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  have v26 : a 26 = (0 + (1 / 3) * Real.sqrt 3) := by
    rw [show 26 = 24 + 2 by decide, h₂ 24 (by norm_num), v24, v25]
    have hd : 1 - (-2 + 1 * Real.sqrt 3) * (1 + 0 * Real.sqrt 3) ≠ 0 := by
      intro he
      nlinarith only [hs, hsp, he]
    apply (div_eq_iff hd).mpr
    nlinarith only [hs, hs3]
  refine ⟨?_, ?_, ?_⟩
  · simpa using v17
  · simpa using v25
  · exact v26.trans (v2.symm.trans h₁)


set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 1 = 1) (h₁ : a 2 = 1 / Real.sqrt 3)
  (h₂ : ∀ n, 1 ≤ n → a (n + 2) = (a n + a (n + 1)) / (1 - a n * a (n + 1))) (node_initial_period : a 17 = 0 ∧ a 25 = 1 ∧ a 26 = 1 / Real.sqrt 3) : ∀ n : ℕ, a (n + 25) = a (n + 1) ∧ a (n + 26) = a (n + 2) := by
  intro n
  induction n with
  | zero => simpa [h₀, h₁] using node_initial_period.2
  | succ n ih =>
      constructor
      · simpa [Nat.succ_eq_add_one, Nat.add_assoc] using ih.2
      · change a (n + 27) = a (n + 3)
        have hh := h₂ (n + 25) (by omega)
        have hl := h₂ (n + 1) (by omega)
        simp only [Nat.add_assoc, Nat.reduceAdd] at hh hl
        rw [ih.1, ih.2] at hh
        exact hh.trans hl.symm

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℝ) (h₀ : a 1 = 1) (h₁ : a 2 = 1 / Real.sqrt 3)
  (h₂ : ∀ n, 1 ≤ n → a (n + 2) = (a n + a (n + 1)) / (1 - a n * a (n + 1))) (node_initial_period : a 17 = 0 ∧ a 25 = 1 ∧ a 26 = 1 / Real.sqrt 3) (node_period_twenty_four : ∀ n : ℕ, a (n + 25) = a (n + 1) ∧ a (n + 26) = a (n + 2)) : abs (a 2009) = 0 := by
  have hm : ∀ m : ℕ, a (24 * m + 17) = a 17 := by
    intro m
    induction m with
    | zero => rfl
    | succ m ih =>
        have hp := (node_period_twenty_four (24 * m + 16)).1
        have he : 24 * (m + 1) + 17 = 24 * m + 16 + 25 := by omega
        rw [he, hp]
        simpa [Nat.add_assoc] using ih
  have hv : a 2009 = 0 := (hm 83).trans node_initial_period.1
  rw [hv, abs_zero]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2009_p25 (a : ℕ → ℝ) (h₀ : a 1 = 1) (h₁ : a 2 = 1 / Real.sqrt 3)
  (h₂ : ∀ n, 1 ≤ n → a (n + 2) = (a n + a (n + 1)) / (1 - a n * a (n + 1))) : abs (a 2009) = 0 := by
  have node_initial_period : a 17 = 0 ∧ a 25 = 1 ∧ a 26 = 1 / Real.sqrt 3 := by
    have hs : (Real.sqrt 3) ^ 2 = (3 : ℝ) := Real.sq_sqrt (by norm_num)
    have hsp : 0 < Real.sqrt 3 := Real.sqrt_pos.mpr (by norm_num)
    have hs3 : (Real.sqrt 3) ^ 3 = 3 * Real.sqrt 3 := by
      calc (Real.sqrt 3) ^ 3 = (Real.sqrt 3) ^ 2 * Real.sqrt 3 := by ring
           _ = 3 * Real.sqrt 3 := by rw [hs]
    have v1 : a 1 = (1 + 0 * Real.sqrt 3) := by simpa using h₀
    have v2 : a 2 = (0 + (1 / 3) * Real.sqrt 3) := by
      rw [h₁]
      field_simp
      nlinarith [hs]
    have v3 : a 3 = (2 + 1 * Real.sqrt 3) := by
      rw [show 3 = 1 + 2 by decide, h₂ 1 (by norm_num), v1, v2]
      have hd : 1 - (1 + 0 * Real.sqrt 3) * (0 + (1 / 3) * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v4 : a 4 = (-2 + -1 * Real.sqrt 3) := by
      rw [show 4 = 2 + 2 by decide, h₂ 2 (by norm_num), v2, v3]
      have hd : 1 - (0 + (1 / 3) * Real.sqrt 3) * (2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v5 : a 5 = (0 + 0 * Real.sqrt 3) := by
      rw [show 5 = 3 + 2 by decide, h₂ 3 (by norm_num), v3, v4]
      have hd : 1 - (2 + 1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v6 : a 6 = (-2 + -1 * Real.sqrt 3) := by
      rw [show 6 = 4 + 2 by decide, h₂ 4 (by norm_num), v4, v5]
      have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v7 : a 7 = (-2 + -1 * Real.sqrt 3) := by
      rw [show 7 = 5 + 2 by decide, h₂ 5 (by norm_num), v5, v6]
      have hd : 1 - (0 + 0 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v8 : a 8 = (0 + (1 / 3) * Real.sqrt 3) := by
      rw [show 8 = 6 + 2 by decide, h₂ 6 (by norm_num), v6, v7]
      have hd : 1 - (-2 + -1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v9 : a 9 = (-1 + 0 * Real.sqrt 3) := by
      rw [show 9 = 7 + 2 by decide, h₂ 7 (by norm_num), v7, v8]
      have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + (1 / 3) * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v10 : a 10 = (-2 + 1 * Real.sqrt 3) := by
      rw [show 10 = 8 + 2 by decide, h₂ 8 (by norm_num), v8, v9]
      have hd : 1 - (0 + (1 / 3) * Real.sqrt 3) * (-1 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v11 : a 11 = (0 + -1 * Real.sqrt 3) := by
      rw [show 11 = 9 + 2 by decide, h₂ 9 (by norm_num), v9, v10]
      have hd : 1 - (-1 + 0 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v12 : a 12 = (-2 + -1 * Real.sqrt 3) := by
      rw [show 12 = 10 + 2 by decide, h₂ 10 (by norm_num), v10, v11]
      have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v13 : a 13 = (1 + 0 * Real.sqrt 3) := by
      rw [show 13 = 11 + 2 by decide, h₂ 11 (by norm_num), v11, v12]
      have hd : 1 - (0 + -1 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v14 : a 14 = (0 + (-1 / 3) * Real.sqrt 3) := by
      rw [show 14 = 12 + 2 by decide, h₂ 12 (by norm_num), v12, v13]
      have hd : 1 - (-2 + -1 * Real.sqrt 3) * (1 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v15 : a 15 = (2 + -1 * Real.sqrt 3) := by
      rw [show 15 = 13 + 2 by decide, h₂ 13 (by norm_num), v13, v14]
      have hd : 1 - (1 + 0 * Real.sqrt 3) * (0 + (-1 / 3) * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v16 : a 16 = (-2 + 1 * Real.sqrt 3) := by
      rw [show 16 = 14 + 2 by decide, h₂ 14 (by norm_num), v14, v15]
      have hd : 1 - (0 + (-1 / 3) * Real.sqrt 3) * (2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v17 : a 17 = (0 + 0 * Real.sqrt 3) := by
      rw [show 17 = 15 + 2 by decide, h₂ 15 (by norm_num), v15, v16]
      have hd : 1 - (2 + -1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v18 : a 18 = (-2 + 1 * Real.sqrt 3) := by
      rw [show 18 = 16 + 2 by decide, h₂ 16 (by norm_num), v16, v17]
      have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v19 : a 19 = (-2 + 1 * Real.sqrt 3) := by
      rw [show 19 = 17 + 2 by decide, h₂ 17 (by norm_num), v17, v18]
      have hd : 1 - (0 + 0 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v20 : a 20 = (0 + (-1 / 3) * Real.sqrt 3) := by
      rw [show 20 = 18 + 2 by decide, h₂ 18 (by norm_num), v18, v19]
      have hd : 1 - (-2 + 1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v21 : a 21 = (-1 + 0 * Real.sqrt 3) := by
      rw [show 21 = 19 + 2 by decide, h₂ 19 (by norm_num), v19, v20]
      have hd : 1 - (-2 + 1 * Real.sqrt 3) * (0 + (-1 / 3) * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v22 : a 22 = (-2 + -1 * Real.sqrt 3) := by
      rw [show 22 = 20 + 2 by decide, h₂ 20 (by norm_num), v20, v21]
      have hd : 1 - (0 + (-1 / 3) * Real.sqrt 3) * (-1 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v23 : a 23 = (0 + 1 * Real.sqrt 3) := by
      rw [show 23 = 21 + 2 by decide, h₂ 21 (by norm_num), v21, v22]
      have hd : 1 - (-1 + 0 * Real.sqrt 3) * (-2 + -1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v24 : a 24 = (-2 + 1 * Real.sqrt 3) := by
      rw [show 24 = 22 + 2 by decide, h₂ 22 (by norm_num), v22, v23]
      have hd : 1 - (-2 + -1 * Real.sqrt 3) * (0 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v25 : a 25 = (1 + 0 * Real.sqrt 3) := by
      rw [show 25 = 23 + 2 by decide, h₂ 23 (by norm_num), v23, v24]
      have hd : 1 - (0 + 1 * Real.sqrt 3) * (-2 + 1 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    have v26 : a 26 = (0 + (1 / 3) * Real.sqrt 3) := by
      rw [show 26 = 24 + 2 by decide, h₂ 24 (by norm_num), v24, v25]
      have hd : 1 - (-2 + 1 * Real.sqrt 3) * (1 + 0 * Real.sqrt 3) ≠ 0 := by
        intro he
        nlinarith only [hs, hsp, he]
      apply (div_eq_iff hd).mpr
      nlinarith only [hs, hs3]
    refine ⟨?_, ?_, ?_⟩
    · simpa using v17
    · simpa using v25
    · exact v26.trans (v2.symm.trans h₁)
  have node_period_twenty_four : ∀ n : ℕ, a (n + 25) = a (n + 1) ∧ a (n + 26) = a (n + 2) := by
    intro n
    induction n with
    | zero => simpa [h₀, h₁] using node_initial_period.2
    | succ n ih =>
        constructor
        · simpa [Nat.succ_eq_add_one, Nat.add_assoc] using ih.2
        · change a (n + 27) = a (n + 3)
          have hh := h₂ (n + 25) (by omega)
          have hl := h₂ (n + 1) (by omega)
          simp only [Nat.add_assoc, Nat.reduceAdd] at hh hl
          rw [ih.1, ih.2] at hh
          exact hh.trans hl.symm
  have node_root : abs (a 2009) = 0 := by
    have hm : ∀ m : ℕ, a (24 * m + 17) = a 17 := by
      intro m
      induction m with
      | zero => rfl
      | succ m ih =>
          have hp := (node_period_twenty_four (24 * m + 16)).1
          have he : 24 * (m + 1) + 17 = 24 * m + 16 + 25 := by omega
          rw [he, hp]
          simpa [Nat.add_assoc] using ih
    have hv : a 2009 = 0 := (hm 83).trans node_initial_period.1
    rw [hv, abs_zero]
  exact node_root

#print axioms amc12a_2009_p25
