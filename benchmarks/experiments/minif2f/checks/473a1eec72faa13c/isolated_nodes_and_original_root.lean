import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ)
  (n : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : 0 < n) : ∀ k : ℕ, 0 ≤ (a-b)*(a^k-b^k) := by
  intro k
  rcases le_total b a with h | h
  · exact mul_nonneg (sub_nonneg.mpr h) (sub_nonneg.mpr (pow_le_pow_left₀ h₀.2.le h k))
  · exact mul_nonneg_of_nonpos_of_nonpos (sub_nonpos.mpr h) (sub_nonpos.mpr (pow_le_pow_left₀ h₀.1.le h k))

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ)
  (n : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : 0 < n) (node_power_difference_sign : ∀ k : ℕ, 0 ≤ (a-b)*(a^k-b^k)) : ((a + b) / 2)^n ≤ (a^n + b^n) / 2 := by
  have hi : ∀ k : ℕ, ((a+b)/2)^k ≤ (a^k+b^k)/2 := by
    intro k
    induction k with
    | zero => norm_num
    | succ k ih =>
      have hp := mul_le_mul_of_nonneg_right ih (by linarith [h₀.1, h₀.2] : 0 ≤ (a+b)/2)
      rw [pow_succ]
      have hm : (a^k+b^k)*(a+b) ≤ 2*(a^(k+1)+b^(k+1)) := by
        rw [pow_succ, pow_succ]
        nlinarith only [node_power_difference_sign k]
      nlinarith only [hp, hm]
  exact hi n

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_apbon2pownleqapownpbpowon2 (a b : ℝ)
  (n : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : 0 < n) : ((a + b) / 2)^n ≤ (a^n + b^n) / 2 := by
  have node_power_difference_sign : ∀ k : ℕ, 0 ≤ (a-b)*(a^k-b^k) := by
    intro k
    rcases le_total b a with h | h
    · exact mul_nonneg (sub_nonneg.mpr h) (sub_nonneg.mpr (pow_le_pow_left₀ h₀.2.le h k))
    · exact mul_nonneg_of_nonpos_of_nonpos (sub_nonpos.mpr h) (sub_nonpos.mpr (pow_le_pow_left₀ h₀.1.le h k))
  have node_root : ((a + b) / 2)^n ≤ (a^n + b^n) / 2 := by
    have hi : ∀ k : ℕ, ((a+b)/2)^k ≤ (a^k+b^k)/2 := by
      intro k
      induction k with
      | zero => norm_num
      | succ k ih =>
        have hp := mul_le_mul_of_nonneg_right ih (by linarith [h₀.1, h₀.2] : 0 ≤ (a+b)/2)
        rw [pow_succ]
        have hm : (a^k+b^k)*(a+b) ≤ 2*(a^(k+1)+b^(k+1)) := by
          rw [pow_succ, pow_succ]
          nlinarith only [node_power_difference_sign k]
        nlinarith only [hp, hm]
    exact hi n
  exact node_root

#print axioms algebra_apbon2pownleqapownpbpowon2
