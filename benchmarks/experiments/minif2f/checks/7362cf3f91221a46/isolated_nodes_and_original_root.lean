import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℤ) (h₀ : 0 < a ∧ 0 < b) (h₁ : ¬7 ∣ a) (h₂ : ¬7 ∣ b) (h₃ : ¬7 ∣ a + b)
  (h₄ : 7 ^ 7 ∣ (a + b) ^ 7 - a ^ 7 - b ^ 7) : 7 ^ 6 ∣ a * b * (a + b) * (a ^ 2 + a * b + b ^ 2) ^ 2 := by
  have hf : (a + b) ^ 7 - a ^ 7 - b ^ 7 = 7 * (a * b * (a + b) * (a ^ 2 + a * b + b ^ 2) ^ 2) := by ring
  obtain ⟨k, hk⟩ := h₄
  refine ⟨k, ?_⟩
  rw [hf] at hk
  norm_num at hk ⊢
  nlinarith only [hk]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℤ) (h₀ : 0 < a ∧ 0 < b) (h₁ : ¬7 ∣ a) (h₂ : ¬7 ∣ b) (h₃ : ¬7 ∣ a + b)
  (h₄ : 7 ^ 7 ∣ (a + b) ^ 7 - a ^ 7 - b ^ 7) (node_seventh_power_reduction : 7 ^ 6 ∣ a * b * (a + b) * (a ^ 2 + a * b + b ^ 2) ^ 2) : 19 ≤ a + b := by
  by_contra hbound
  have ha : 1 ≤ a := by omega
  have hb : 1 ≤ b := by omega
  have hau : a ≤ 17 := by omega
  have hbu : b ≤ 17 := by omega
  clear h₄
  interval_cases a <;> interval_cases b <;> norm_num at *

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1984_p2 (a b : ℤ) (h₀ : 0 < a ∧ 0 < b) (h₁ : ¬7 ∣ a) (h₂ : ¬7 ∣ b) (h₃ : ¬7 ∣ a + b)
  (h₄ : 7 ^ 7 ∣ (a + b) ^ 7 - a ^ 7 - b ^ 7) : 19 ≤ a + b := by
  have node_seventh_power_reduction : 7 ^ 6 ∣ a * b * (a + b) * (a ^ 2 + a * b + b ^ 2) ^ 2 := by
    have hf : (a + b) ^ 7 - a ^ 7 - b ^ 7 = 7 * (a * b * (a + b) * (a ^ 2 + a * b + b ^ 2) ^ 2) := by ring
    obtain ⟨k, hk⟩ := h₄
    refine ⟨k, ?_⟩
    rw [hf] at hk
    norm_num at hk ⊢
    nlinarith only [hk]
  have node_root : 19 ≤ a + b := by
    by_contra hbound
    have ha : 1 ≤ a := by omega
    have hb : 1 ≤ b := by omega
    have hau : a ≤ 17 := by omega
    have hbu : b ≤ 17 := by omega
    clear h₄
    interval_cases a <;> interval_cases b <;> norm_num at *
  exact node_root

#print axioms imo_1984_p2
