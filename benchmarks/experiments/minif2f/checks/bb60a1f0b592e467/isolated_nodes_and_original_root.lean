import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ) (k : ℝ) (a b : ℕ) (h₀ : ∀ x, f x = x ^ 2 - 63 * x + k)
  (h₁ : f a = 0 ∧ f b = 0) (h₂ : a ≠ b) (h₃ : Nat.Prime a ∧ Nat.Prime b) : a + b = 63 := by
  have hp := h₀ a
  have hq := h₀ b
  have hn : (a : ℝ) ≠ b := by exact_mod_cast h₂
  have hz : ((a : ℝ) - b) * ((a : ℝ) + b - 63) = 0 := by nlinarith [h₁.1, h₁.2]
  have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)
  have hs' : (a : ℝ) + b = 63 := by linarith
  exact_mod_cast hs' 

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ) (k : ℝ) (a b : ℕ) (h₀ : ∀ x, f x = x ^ 2 - 63 * x + k)
  (h₁ : f a = 0 ∧ f b = 0) (h₂ : a ≠ b) (h₃ : Nat.Prime a ∧ Nat.Prime b) (node_prime_root_sum : a + b = 63) : (a = 2 ∧ b = 61) ∨ (a = 61 ∧ b = 2) := by
  have ha := h₃.1.eq_two_or_odd
  have hb := h₃.2.eq_two_or_odd
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ) (k : ℝ) (a b : ℕ) (h₀ : ∀ x, f x = x ^ 2 - 63 * x + k)
  (h₁ : f a = 0 ∧ f b = 0) (h₂ : a ≠ b) (h₃ : Nat.Prime a ∧ Nat.Prime b) (node_parity_forces_prime_pair : (a = 2 ∧ b = 61) ∨ (a = 61 ∧ b = 2)) : k = 122 := by
  have hp := h₀ a
  have hf := h₁.1
  rcases node_parity_forces_prime_pair with ⟨ha, hb⟩ | ⟨ha, hb⟩
  all_goals
    rw [ha] at hp hf
    norm_num at hp hf
    linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2002_p12 (f : ℝ → ℝ) (k : ℝ) (a b : ℕ) (h₀ : ∀ x, f x = x ^ 2 - 63 * x + k)
  (h₁ : f a = 0 ∧ f b = 0) (h₂ : a ≠ b) (h₃ : Nat.Prime a ∧ Nat.Prime b) : k = 122 := by
  have node_prime_root_sum : a + b = 63 := by
    have hp := h₀ a
    have hq := h₀ b
    have hn : (a : ℝ) ≠ b := by exact_mod_cast h₂
    have hz : ((a : ℝ) - b) * ((a : ℝ) + b - 63) = 0 := by nlinarith [h₁.1, h₁.2]
    have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)
    have hs' : (a : ℝ) + b = 63 := by linarith
    exact_mod_cast hs'
  have node_parity_forces_prime_pair : (a = 2 ∧ b = 61) ∨ (a = 61 ∧ b = 2) := by
    have ha := h₃.1.eq_two_or_odd
    have hb := h₃.2.eq_two_or_odd
    omega
  have node_root : k = 122 := by
    have hp := h₀ a
    have hf := h₁.1
    rcases node_parity_forces_prime_pair with ⟨ha, hb⟩ | ⟨ha, hb⟩
    all_goals
      rw [ha] at hp hf
      norm_num at hp hf
      linarith
  exact node_root

#print axioms amc12a_2002_p12
