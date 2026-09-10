import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℕ → ℤ) (h₀ : x 1 = 211) (h₂ : x 2 = 375) (h₃ : x 3 = 420)
  (h₄ : x 4 = 523) (h₆ : ∀ n ≥ 5, x n = x (n - 1) - x (n - 2) + x (n - 3) - x (n - 4)) : ∀ n : ℕ, 1 ≤ n → x (n+5) = -x n := by
  intro n hn
  have h1 := h₆ (n+5) (by omega)
  have h2 := h₆ (n+4) (by omega)
  simp only [show n+5-1=n+4 by omega, show n+5-2=n+3 by omega, show n+5-3=n+2 by omega, show n+5-4=n+1 by omega] at h1
  simp only [show n+4-1=n+3 by omega, show n+4-2=n+2 by omega, show n+4-3=n+1 by omega, show n+4-4=n by omega] at h2
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℕ → ℤ) (h₀ : x 1 = 211) (h₂ : x 2 = 375) (h₃ : x 3 = 420)
  (h₄ : x 4 = 523) (h₆ : ∀ n ≥ 5, x n = x (n - 1) - x (n - 2) + x (n - 3) - x (n - 4)) (node_five_step_negation : ∀ n : ℕ, 1 ≤ n → x (n+5) = -x n) : ∀ k n : ℕ, 1 ≤ n → x (n+10*k) = x n := by
  intro k n hn
  induction k with
  | zero => simp
  | succ k ih =>
    have h1 := node_five_step_negation (n+10*k) (by omega)
    have h2 := node_five_step_negation (n+10*k+5) (by omega)
    have he : n+10*(k+1)=(n+10*k+5)+5 := by omega
    rw [he, h2, h1, neg_neg, ih]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℕ → ℤ) (h₀ : x 1 = 211) (h₂ : x 2 = 375) (h₃ : x 3 = 420)
  (h₄ : x 4 = 523) (h₆ : ∀ n ≥ 5, x n = x (n - 1) - x (n - 2) + x (n - 3) - x (n - 4)) (node_iterated_period : ∀ k n : ℕ, 1 ≤ n → x (n+10*k) = x n) : x 531 + x 753 + x 975 = 898 := by
  have hx5 := h₆ 5 (by norm_num)
  norm_num [h₀, h₂, h₃, h₄] at hx5
  have h1 := node_iterated_period 53 1 (by norm_num)
  have h3 := node_iterated_period 75 3 (by norm_num)
  have h5 := node_iterated_period 97 5 (by norm_num)
  norm_num [h₀, h₃, hx5] at h1 h3 h5
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aimeII_2001_p3 (x : ℕ → ℤ) (h₀ : x 1 = 211) (h₂ : x 2 = 375) (h₃ : x 3 = 420)
  (h₄ : x 4 = 523) (h₆ : ∀ n ≥ 5, x n = x (n - 1) - x (n - 2) + x (n - 3) - x (n - 4)) : x 531 + x 753 + x 975 = 898 := by
  have node_five_step_negation : ∀ n : ℕ, 1 ≤ n → x (n+5) = -x n := by
    intro n hn
    have h1 := h₆ (n+5) (by omega)
    have h2 := h₆ (n+4) (by omega)
    simp only [show n+5-1=n+4 by omega, show n+5-2=n+3 by omega, show n+5-3=n+2 by omega, show n+5-4=n+1 by omega] at h1
    simp only [show n+4-1=n+3 by omega, show n+4-2=n+2 by omega, show n+4-3=n+1 by omega, show n+4-4=n by omega] at h2
    omega
  have node_iterated_period : ∀ k n : ℕ, 1 ≤ n → x (n+10*k) = x n := by
    intro k n hn
    induction k with
    | zero => simp
    | succ k ih =>
      have h1 := node_five_step_negation (n+10*k) (by omega)
      have h2 := node_five_step_negation (n+10*k+5) (by omega)
      have he : n+10*(k+1)=(n+10*k+5)+5 := by omega
      rw [he, h2, h1, neg_neg, ih]
  have node_root : x 531 + x 753 + x 975 = 898 := by
    have hx5 := h₆ 5 (by norm_num)
    norm_num [h₀, h₂, h₃, h₄] at hx5
    have h1 := node_iterated_period 53 1 (by norm_num)
    have h3 := node_iterated_period 75 3 (by norm_num)
    have h5 := node_iterated_period 97 5 (by norm_num)
    norm_num [h₀, h₃, hx5] at h1 h3 h5
    omega
  exact node_root

#print axioms aimeII_2001_p3
