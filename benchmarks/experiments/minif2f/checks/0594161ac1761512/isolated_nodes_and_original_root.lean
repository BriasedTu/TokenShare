import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ → ℝ)
  (h₀ : ∀ n, a (n + 1) = Real.sqrt 3 * a n - b n)
  (h₁ : ∀ n, b (n + 1) = Real.sqrt 3 * b n + a n)
  (h₂ : a 100 = 2)
  (h₃ : b 100 = 4) : ∀ n : ℕ, a (n + 3) = -8 * b n ∧ b (n + 3) = 8 * a n := by
  intro n
  have hs : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  constructor
  · rw [show n + 3 = (n + 2) + 1 by omega, h₀, h₀ (n + 1), h₁ (n + 1), h₀ n, h₁ n]
    linear_combination (Real.sqrt 3 * a n - 3 * b n) * hs
  · rw [show n + 3 = (n + 2) + 1 by omega, h₁, h₁ (n + 1), h₀ (n + 1), h₁ n, h₀ n]
    linear_combination (Real.sqrt 3 * b n + 3 * a n) * hs

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ → ℝ)
  (h₀ : ∀ n, a (n + 1) = Real.sqrt 3 * a n - b n)
  (h₁ : ∀ n, b (n + 1) = Real.sqrt 3 * b n + a n)
  (h₂ : a 100 = 2)
  (h₃ : b 100 = 4) (node_three_step_rotation : ∀ n : ℕ, a (n + 3) = -8 * b n ∧ b (n + 3) = 8 * a n) : ∀ k n : ℕ, a (n + 6 * k) = (-64 : ℝ) ^ k * a n ∧ b (n + 6 * k) = (-64 : ℝ) ^ k * b n := by
  have hstep : ∀ n : ℕ, a (n + 6) = -64 * a n ∧ b (n + 6) = -64 * b n := by
    intro n
    have ha := node_three_step_rotation (n + 3)
    have hb := node_three_step_rotation n
    have hn : n + 3 + 3 = n + 6 := by omega
    rw [hn] at ha
    constructor <;> nlinarith [ha.1, ha.2, hb.1, hb.2]
  intro k
  induction k with
  | zero => intro n; simp
  | succ k ih =>
    intro n
    have hs := hstep (n + 6 * k)
    have hi := ih n
    rw [show n + 6 * (k + 1) = (n + 6 * k) + 6 by omega]
    constructor
    · rw [hs.1, hi.1, pow_succ]; ring
    · rw [hs.2, hi.2, pow_succ]; ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ → ℝ)
  (h₀ : ∀ n, a (n + 1) = Real.sqrt 3 * a n - b n)
  (h₁ : ∀ n, b (n + 1) = Real.sqrt 3 * b n + a n)
  (h₂ : a 100 = 2)
  (h₃ : b 100 = 4) (node_three_step_rotation : ∀ n : ℕ, a (n + 3) = -8 * b n ∧ b (n + 3) = 8 * a n) (node_iterated_six_step_scaling : ∀ k n : ℕ, a (n + 6 * k) = (-64 : ℝ) ^ k * a n ∧ b (n + 6 * k) = (-64 : ℝ) ^ k * b n) : a 1 + b 1 = 1 / (2^98) := by
  have hs := node_iterated_six_step_scaling 16 4
  have hr := node_three_step_rotation 1
  norm_num at hs hr ⊢
  nlinarith [hs.1, hs.2, hr.1, hr.2]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2008_p25 (a b : ℕ → ℝ)
  (h₀ : ∀ n, a (n + 1) = Real.sqrt 3 * a n - b n)
  (h₁ : ∀ n, b (n + 1) = Real.sqrt 3 * b n + a n)
  (h₂ : a 100 = 2)
  (h₃ : b 100 = 4) : a 1 + b 1 = 1 / (2^98) := by
  have node_three_step_rotation : ∀ n : ℕ, a (n + 3) = -8 * b n ∧ b (n + 3) = 8 * a n := by
    intro n
    have hs : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
    constructor
    · rw [show n + 3 = (n + 2) + 1 by omega, h₀, h₀ (n + 1), h₁ (n + 1), h₀ n, h₁ n]
      linear_combination (Real.sqrt 3 * a n - 3 * b n) * hs
    · rw [show n + 3 = (n + 2) + 1 by omega, h₁, h₁ (n + 1), h₀ (n + 1), h₁ n, h₀ n]
      linear_combination (Real.sqrt 3 * b n + 3 * a n) * hs
  have node_iterated_six_step_scaling : ∀ k n : ℕ, a (n + 6 * k) = (-64 : ℝ) ^ k * a n ∧ b (n + 6 * k) = (-64 : ℝ) ^ k * b n := by
    have hstep : ∀ n : ℕ, a (n + 6) = -64 * a n ∧ b (n + 6) = -64 * b n := by
      intro n
      have ha := node_three_step_rotation (n + 3)
      have hb := node_three_step_rotation n
      have hn : n + 3 + 3 = n + 6 := by omega
      rw [hn] at ha
      constructor <;> nlinarith [ha.1, ha.2, hb.1, hb.2]
    intro k
    induction k with
    | zero => intro n; simp
    | succ k ih =>
      intro n
      have hs := hstep (n + 6 * k)
      have hi := ih n
      rw [show n + 6 * (k + 1) = (n + 6 * k) + 6 by omega]
      constructor
      · rw [hs.1, hi.1, pow_succ]; ring
      · rw [hs.2, hi.2, pow_succ]; ring
  have node_root : a 1 + b 1 = 1 / (2^98) := by
    have hs := node_iterated_six_step_scaling 16 4
    have hr := node_three_step_rotation 1
    norm_num at hs hr ⊢
    nlinarith [hs.1, hs.2, hr.1, hr.2]
  exact node_root

#print axioms amc12a_2008_p25
