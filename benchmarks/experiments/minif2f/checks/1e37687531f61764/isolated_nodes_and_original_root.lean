import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (m n : ℕ) (k : ℝ) (f : ℝ → ℝ) (h₀ : Nat.Prime m) (h₁ : Nat.Prime n)
  (h₂ : ∀ x, f x = x ^ 2 - 12 * x + k) (h₃ : f m = 0) (h₄ : f n = 0) (h₅ : m ≠ n) : m + n = 12 := by
  have hp := h₂ m
  have hq := h₂ n
  have hn : (m : ℝ) ≠ n := by exact_mod_cast h₅
  have hz : ((m : ℝ) - n) * ((m : ℝ) + n - 12) = 0 := by nlinarith
  have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)
  have hs' : (m : ℝ) + n = 12 := by linarith
  exact_mod_cast hs' 

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (m n : ℕ) (k : ℝ) (f : ℝ → ℝ) (h₀ : Nat.Prime m) (h₁ : Nat.Prime n)
  (h₂ : ∀ x, f x = x ^ 2 - 12 * x + k) (h₃ : f m = 0) (h₄ : f n = 0) (h₅ : m ≠ n) (node_prime_root_sum : m + n = 12) : (m = 5 ∧ n = 7) ∨ (m = 7 ∧ n = 5) := by
  have hm : m ≤ 12 := by omega
  have hn : n ≤ 12 := by omega
  interval_cases m <;> interval_cases n <;> norm_num at *

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (m n : ℕ) (k : ℝ) (f : ℝ → ℝ) (h₀ : Nat.Prime m) (h₁ : Nat.Prime n)
  (h₂ : ∀ x, f x = x ^ 2 - 12 * x + k) (h₃ : f m = 0) (h₄ : f n = 0) (h₅ : m ≠ n) (node_admissible_prime_pairs : (m = 5 ∧ n = 7) ∨ (m = 7 ∧ n = 5)) : k = 35 := by
  have hp := h₂ m
  rcases node_admissible_prime_pairs with ⟨hm, hn⟩ | ⟨hm, hn⟩
  all_goals
    rw [hm] at hp h₃
    norm_num at hp h₃
    linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_482 (m n : ℕ) (k : ℝ) (f : ℝ → ℝ) (h₀ : Nat.Prime m) (h₁ : Nat.Prime n)
  (h₂ : ∀ x, f x = x ^ 2 - 12 * x + k) (h₃ : f m = 0) (h₄ : f n = 0) (h₅ : m ≠ n) : k = 35 := by
  have node_prime_root_sum : m + n = 12 := by
    have hp := h₂ m
    have hq := h₂ n
    have hn : (m : ℝ) ≠ n := by exact_mod_cast h₅
    have hz : ((m : ℝ) - n) * ((m : ℝ) + n - 12) = 0 := by nlinarith
    have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)
    have hs' : (m : ℝ) + n = 12 := by linarith
    exact_mod_cast hs'
  have node_admissible_prime_pairs : (m = 5 ∧ n = 7) ∨ (m = 7 ∧ n = 5) := by
    have hm : m ≤ 12 := by omega
    have hn : n ≤ 12 := by omega
    interval_cases m <;> interval_cases n <;> norm_num at *
  have node_root : k = 35 := by
    have hp := h₂ m
    rcases node_admissible_prime_pairs with ⟨hm, hn⟩ | ⟨hm, hn⟩
    all_goals
      rw [hm] at hp h₃
      norm_num at hp h₃
      linarith
  exact node_root

#print axioms mathd_algebra_482
