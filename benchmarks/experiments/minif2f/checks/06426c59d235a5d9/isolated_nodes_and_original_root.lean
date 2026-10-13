import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b q r : ℕ) (h₀ : r < a + b) (h₁ : a ^ 2 + b ^ 2 = (a + b) * q + r)
  (h₂ : q ^ 2 + r = 1977) : q = 44 := by
  have hp : 0 < a + b := by omega
  have hc : (a + b) ^ 2 < 2 * (q + 1) * (a + b) := by
    nlinarith [sq_nonneg ((a : ℤ) - (b : ℤ))]
  have hs : a + b < 2 * (q + 1) := by
    by_contra hn
    have hm := Nat.mul_le_mul_right (a + b) (show 2 * (q + 1) ≤ a + b by omega)
    nlinarith
  have hu : q ≤ 44 := by nlinarith
  have hl : 44 ≤ q := by
    by_contra hn
    have hq : q ≤ 43 := by omega
    have hq2 := Nat.mul_le_mul hq hq
    nlinarith
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b q r : ℕ) (h₀ : r < a + b) (h₁ : a ^ 2 + b ^ 2 = (a + b) * q + r)
  (h₂ : q ^ 2 + r = 1977) (node_quotient_identification : q = 44) : abs ((a : ℤ) - 22) = 15 ∧ abs ((b : ℤ) - 22) = 28 ∨
    abs ((a : ℤ) - 22) = 28 ∧ abs ((b : ℤ) - 22) = 15 := by
  rw [node_quotient_identification] at h₁ h₂
  have hr : r = 41 := by omega
  subst r
  have ha : a ≤ 66 := by nlinarith [sq_nonneg ((b : ℤ) - 22)]
  have hb : b ≤ 66 := by nlinarith [sq_nonneg ((a : ℤ) - 22)]
  interval_cases a <;> interval_cases b <;> norm_num at *

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1977_p5 (a b q r : ℕ) (h₀ : r < a + b) (h₁ : a ^ 2 + b ^ 2 = (a + b) * q + r)
  (h₂ : q ^ 2 + r = 1977) : abs ((a : ℤ) - 22) = 15 ∧ abs ((b : ℤ) - 22) = 28 ∨
    abs ((a : ℤ) - 22) = 28 ∧ abs ((b : ℤ) - 22) = 15 := by
  have node_quotient_identification : q = 44 := by
    have hp : 0 < a + b := by omega
    have hc : (a + b) ^ 2 < 2 * (q + 1) * (a + b) := by
      nlinarith [sq_nonneg ((a : ℤ) - (b : ℤ))]
    have hs : a + b < 2 * (q + 1) := by
      by_contra hn
      have hm := Nat.mul_le_mul_right (a + b) (show 2 * (q + 1) ≤ a + b by omega)
      nlinarith
    have hu : q ≤ 44 := by nlinarith
    have hl : 44 ≤ q := by
      by_contra hn
      have hq : q ≤ 43 := by omega
      have hq2 := Nat.mul_le_mul hq hq
      nlinarith
    omega
  have node_root : abs ((a : ℤ) - 22) = 15 ∧ abs ((b : ℤ) - 22) = 28 ∨
    abs ((a : ℤ) - 22) = 28 ∧ abs ((b : ℤ) - 22) = 15 := by
    rw [node_quotient_identification] at h₁ h₂
    have hr : r = 41 := by omega
    subst r
    have ha : a ≤ 66 := by nlinarith [sq_nonneg ((b : ℤ) - 22)]
    have hb : b ≤ 66 := by nlinarith [sq_nonneg ((a : ℤ) - 22)]
    interval_cases a <;> interval_cases b <;> norm_num at *
  exact node_root

#print axioms imo_1977_p5
