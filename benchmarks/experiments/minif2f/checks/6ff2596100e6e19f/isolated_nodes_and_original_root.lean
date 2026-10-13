import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (p q r : ℤ)
  (h₀ : 1 < p ∧ p < q ∧ q < r)
  (h₁ : (p - 1) * (q - 1) * (r - 1)∣(p * q * r - 1)) : ∃ k : ℤ, (k = 2 ∨ k = 3) ∧ p ≤ 3 ∧ p * q * r - 1 = (p - 1) * (q - 1) * (r - 1) * k := by
  obtain ⟨k, hk⟩ := h₁
  have hp : 2 ≤ p := by omega
  have hq : 3 ≤ q := by omega
  have hr : 4 ≤ r := by omega
  have hd : 0 < (p - 1) * (q - 1) * (r - 1) := mul_pos (mul_pos (by omega) (by omega)) (by omega)
  have hlo : (p - 1) * (q - 1) * (r - 1) < p * q * r - 1 := by
    nlinarith [mul_pos (show 0 < p by omega) (show 0 < q - 1 by omega), mul_nonneg (show 0 ≤ q by omega) (show 0 ≤ r - 1 by omega), mul_nonneg (show 0 ≤ r by omega) (show 0 ≤ p - 1 by omega)]
  have hu : p * q * r ≤ 4 * ((p - 1) * (q - 1) * (r - 1)) := by
    nlinarith [mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ q - 3 by omega), mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ r - 4 by omega), mul_nonneg (show 0 ≤ q - 3 by omega) (show 0 ≤ r - 4 by omega), mul_nonneg (mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ q - 3 by omega)) (show 0 ≤ r - 4 by omega)]
  have hklo : 2 ≤ k := by nlinarith
  have hkhi : k ≤ 3 := by nlinarith
  have hps : p ≤ 3 := by
    by_contra hn
    have hp4 : 4 ≤ p := by omega
    have hq5 : 5 ≤ q := by omega
    have hr6 : 6 ≤ r := by omega
    have htwo : p * q * r ≤ 2 * ((p - 1) * (q - 1) * (r - 1)) := by
      nlinarith [mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ q - 5 by omega), mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ r - 6 by omega), mul_nonneg (show 0 ≤ q - 5 by omega) (show 0 ≤ r - 6 by omega), mul_nonneg (mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ q - 5 by omega)) (show 0 ≤ r - 6 by omega)]
    nlinarith
  exact ⟨k, by omega, hps, hk⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (p q r : ℤ)
  (h₀ : 1 < p ∧ p < q ∧ q < r)
  (h₁ : (p - 1) * (q - 1) * (r - 1)∣(p * q * r - 1)) (node_quotient_and_smallest_factor_bounds : ∃ k : ℤ, (k = 2 ∨ k = 3) ∧ p ≤ 3 ∧ p * q * r - 1 = (p - 1) * (q - 1) * (r - 1) * k) : (p, q, r) = (2, 4, 8) ∨ (p, q, r) = (3, 5, 15) := by
  obtain ⟨k, hk, hp, he⟩ := node_quotient_and_smallest_factor_bounds
  have hp2 : 2 ≤ p := by omega
  interval_cases p <;> rcases hk with rfl | rfl
  · nlinarith
  · have hqlo : 4 ≤ q := by nlinarith
    have hqhi : q ≤ 4 := by
      by_contra hn
      have hq5 : 5 ≤ q := by omega
      have hr6 : 6 ≤ r := by omega
      nlinarith [mul_nonneg (show 0 ≤ q - 5 by omega) (show 0 ≤ r - 6 by omega)]
    have hq4 : q = 4 := by omega
    subst q
    have hr8 : r = 8 := by nlinarith
    simp [hr8]
  · have hqlo : 5 ≤ q := by nlinarith
    have hqhi : q ≤ 7 := by
      by_contra hn
      have hq8 : 8 ≤ q := by omega
      have hr9 : 9 ≤ r := by omega
      nlinarith [mul_nonneg (show 0 ≤ q - 8 by omega) (show 0 ≤ r - 9 by omega)]
    interval_cases q
    · have hr15 : r = 15 := by nlinarith
      simp [hr15]
    · omega
    · omega
  · nlinarith [mul_nonneg (show 0 ≤ q - 4 by omega) (show 0 ≤ r - 5 by omega)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1992_p1 (p q r : ℤ)
  (h₀ : 1 < p ∧ p < q ∧ q < r)
  (h₁ : (p - 1) * (q - 1) * (r - 1)∣(p * q * r - 1)) : (p, q, r) = (2, 4, 8) ∨ (p, q, r) = (3, 5, 15) := by
  have node_quotient_and_smallest_factor_bounds : ∃ k : ℤ, (k = 2 ∨ k = 3) ∧ p ≤ 3 ∧ p * q * r - 1 = (p - 1) * (q - 1) * (r - 1) * k := by
    obtain ⟨k, hk⟩ := h₁
    have hp : 2 ≤ p := by omega
    have hq : 3 ≤ q := by omega
    have hr : 4 ≤ r := by omega
    have hd : 0 < (p - 1) * (q - 1) * (r - 1) := mul_pos (mul_pos (by omega) (by omega)) (by omega)
    have hlo : (p - 1) * (q - 1) * (r - 1) < p * q * r - 1 := by
      nlinarith [mul_pos (show 0 < p by omega) (show 0 < q - 1 by omega), mul_nonneg (show 0 ≤ q by omega) (show 0 ≤ r - 1 by omega), mul_nonneg (show 0 ≤ r by omega) (show 0 ≤ p - 1 by omega)]
    have hu : p * q * r ≤ 4 * ((p - 1) * (q - 1) * (r - 1)) := by
      nlinarith [mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ q - 3 by omega), mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ r - 4 by omega), mul_nonneg (show 0 ≤ q - 3 by omega) (show 0 ≤ r - 4 by omega), mul_nonneg (mul_nonneg (show 0 ≤ p - 2 by omega) (show 0 ≤ q - 3 by omega)) (show 0 ≤ r - 4 by omega)]
    have hklo : 2 ≤ k := by nlinarith
    have hkhi : k ≤ 3 := by nlinarith
    have hps : p ≤ 3 := by
      by_contra hn
      have hp4 : 4 ≤ p := by omega
      have hq5 : 5 ≤ q := by omega
      have hr6 : 6 ≤ r := by omega
      have htwo : p * q * r ≤ 2 * ((p - 1) * (q - 1) * (r - 1)) := by
        nlinarith [mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ q - 5 by omega), mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ r - 6 by omega), mul_nonneg (show 0 ≤ q - 5 by omega) (show 0 ≤ r - 6 by omega), mul_nonneg (mul_nonneg (show 0 ≤ p - 4 by omega) (show 0 ≤ q - 5 by omega)) (show 0 ≤ r - 6 by omega)]
      nlinarith
    exact ⟨k, by omega, hps, hk⟩
  have node_root : (p, q, r) = (2, 4, 8) ∨ (p, q, r) = (3, 5, 15) := by
    obtain ⟨k, hk, hp, he⟩ := node_quotient_and_smallest_factor_bounds
    have hp2 : 2 ≤ p := by omega
    interval_cases p <;> rcases hk with rfl | rfl
    · nlinarith
    · have hqlo : 4 ≤ q := by nlinarith
      have hqhi : q ≤ 4 := by
        by_contra hn
        have hq5 : 5 ≤ q := by omega
        have hr6 : 6 ≤ r := by omega
        nlinarith [mul_nonneg (show 0 ≤ q - 5 by omega) (show 0 ≤ r - 6 by omega)]
      have hq4 : q = 4 := by omega
      subst q
      have hr8 : r = 8 := by nlinarith
      simp [hr8]
    · have hqlo : 5 ≤ q := by nlinarith
      have hqhi : q ≤ 7 := by
        by_contra hn
        have hq8 : 8 ≤ q := by omega
        have hr9 : 9 ≤ r := by omega
        nlinarith [mul_nonneg (show 0 ≤ q - 8 by omega) (show 0 ≤ r - 9 by omega)]
      interval_cases q
      · have hr15 : r = 15 := by nlinarith
        simp [hr15]
      · omega
      · omega
    · nlinarith [mul_nonneg (show 0 ≤ q - 4 by omega) (show 0 ≤ r - 5 by omega)]
  exact node_root

#print axioms imo_1992_p1
