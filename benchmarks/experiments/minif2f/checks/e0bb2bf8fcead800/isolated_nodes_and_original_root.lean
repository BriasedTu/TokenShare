import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ)
  (hn : n > 0)
  (p : ℕ → ℕ)
  (h₀ : ∀ x, p x = x^2 - x + 41)
  (h₁ : 1 < Nat.gcd (p n) (p (n+1))) : Nat.gcd (p n) (p (n + 1)) = Nat.gcd (p n) (2 * n) := by
  have hn2 : n ≤ n ^ 2 := by nlinarith
  have hn12 : n + 1 ≤ (n + 1) ^ 2 := by nlinarith
  have e0 := Nat.sub_add_cancel hn2
  have e1 := Nat.sub_add_cancel hn12
  have hd : p (n + 1) = p n + 2 * n := by
    rw [h₀, h₀]
    nlinarith
  rw [hd, Nat.add_comm (p n) (2 * n), Nat.gcd_add_self_right]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ)
  (hn : n > 0)
  (p : ℕ → ℕ)
  (h₀ : ∀ x, p x = x^2 - x + 41)
  (h₁ : 1 < Nat.gcd (p n) (p (n+1))) (node_euclidean_reduction : Nat.gcd (p n) (p (n + 1)) = Nat.gcd (p n) (2 * n)) : 41 ≤ n := by
  by_contra hnlarge
  have hnsmall : n ≤ 40 := by omega
  have h := h₁
  rw [node_euclidean_reduction] at h
  interval_cases n <;> norm_num [h₀] at h

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_618 (n : ℕ)
  (hn : n > 0)
  (p : ℕ → ℕ)
  (h₀ : ∀ x, p x = x^2 - x + 41)
  (h₁ : 1 < Nat.gcd (p n) (p (n+1))) : 41 ≤ n := by
  have node_euclidean_reduction : Nat.gcd (p n) (p (n + 1)) = Nat.gcd (p n) (2 * n) := by
    have hn2 : n ≤ n ^ 2 := by nlinarith
    have hn12 : n + 1 ≤ (n + 1) ^ 2 := by nlinarith
    have e0 := Nat.sub_add_cancel hn2
    have e1 := Nat.sub_add_cancel hn12
    have hd : p (n + 1) = p n + 2 * n := by
      rw [h₀, h₀]
      nlinarith
    rw [hd, Nat.add_comm (p n) (2 * n), Nat.gcd_add_self_right]
  have node_root : 41 ≤ n := by
    by_contra hnlarge
    have hnsmall : n ≤ 40 := by omega
    have h := h₁
    rw [node_euclidean_reduction] at h
    interval_cases n <;> norm_num [h₀] at h
  exact node_root

#print axioms mathd_numbertheory_618
