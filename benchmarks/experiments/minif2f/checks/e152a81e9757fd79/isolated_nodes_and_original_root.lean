import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ)
  (h₀ : ∀ n, 0 < f n)
  (h₁ : ∀ n, 0 < n → f (f n) < f (n + 1)) : ∀ n : ℕ, 0 < n → n ≤ f n := by
  have inv : ∀ k n : ℕ, 0 < n → f n ≤ k → n ≤ k := by
    intro k
    induction k with
    | zero =>
      intro n hn hf
      have := h₀ n
      omega
    | succ k ih =>
      intro n hn hf
      by_cases he : n = 1
      · omega
      have hn' : 0 < n - 1 := by omega
      have hs := h₁ (n - 1) hn'
      have he' : n - 1 + 1 = n := by omega
      rw [he'] at hs
      have inner : f (f (n - 1)) ≤ k := by omega
      have middle : f (n - 1) ≤ k := ih _ (h₀ _) inner
      have lower : n - 1 ≤ k := ih _ hn' middle
      omega
  intro n hn
  exact inv (f n) n hn le_rfl

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ)
  (h₀ : ∀ n, 0 < f n)
  (h₁ : ∀ n, 0 < n → f (f n) < f (n + 1)) (node_pointwise_lower_bound : ∀ n : ℕ, 0 < n → n ≤ f n) : ∀ i j : ℕ, 0 < i → i ≤ j → f i ≤ f j := by
  intro i j hi hij
  induction j, hij using Nat.le_induction with
  | base => exact le_rfl
  | succ j hj ih =>
    have hjpos : 0 < j := by omega
    have hc := h₁ j hjpos
    have hl := node_pointwise_lower_bound (f j) (h₀ j)
    omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ)
  (h₀ : ∀ n, 0 < f n)
  (h₁ : ∀ n, 0 < n → f (f n) < f (n + 1)) (node_pointwise_lower_bound : ∀ n : ℕ, 0 < n → n ≤ f n) (node_positive_monotonicity : ∀ i j : ℕ, 0 < i → i ≤ j → f i ≤ f j) : ∀ n, 0 < n → f n = n := by
  intro n hn
  have hlo := node_pointwise_lower_bound n hn
  by_contra hne
  have hstep : n + 1 ≤ f n := by omega
  have hm := node_positive_monotonicity (n + 1) (f n) (by omega) hstep
  have hc := h₁ n hn
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1977_p6 (f : ℕ → ℕ)
  (h₀ : ∀ n, 0 < f n)
  (h₁ : ∀ n, 0 < n → f (f n) < f (n + 1)) : ∀ n, 0 < n → f n = n := by
  have node_pointwise_lower_bound : ∀ n : ℕ, 0 < n → n ≤ f n := by
    have inv : ∀ k n : ℕ, 0 < n → f n ≤ k → n ≤ k := by
      intro k
      induction k with
      | zero =>
        intro n hn hf
        have := h₀ n
        omega
      | succ k ih =>
        intro n hn hf
        by_cases he : n = 1
        · omega
        have hn' : 0 < n - 1 := by omega
        have hs := h₁ (n - 1) hn'
        have he' : n - 1 + 1 = n := by omega
        rw [he'] at hs
        have inner : f (f (n - 1)) ≤ k := by omega
        have middle : f (n - 1) ≤ k := ih _ (h₀ _) inner
        have lower : n - 1 ≤ k := ih _ hn' middle
        omega
    intro n hn
    exact inv (f n) n hn le_rfl
  have node_positive_monotonicity : ∀ i j : ℕ, 0 < i → i ≤ j → f i ≤ f j := by
    intro i j hi hij
    induction j, hij using Nat.le_induction with
    | base => exact le_rfl
    | succ j hj ih =>
      have hjpos : 0 < j := by omega
      have hc := h₁ j hjpos
      have hl := node_pointwise_lower_bound (f j) (h₀ j)
      omega
  have node_root : ∀ n, 0 < n → f n = n := by
    intro n hn
    have hlo := node_pointwise_lower_bound n hn
    by_contra hne
    have hstep : n + 1 ≤ f n := by omega
    have hm := node_positive_monotonicity (n + 1) (f n) (by omega) hstep
    have hc := h₁ n hn
    omega
  exact node_root

#print axioms imo_1977_p6
