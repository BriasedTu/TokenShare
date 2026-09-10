import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k : ℕ) (h₀ : k = 2008 ^ 2 + 2 ^ 2008) : ∀ m : ℕ, 2 ^ (4 * m + 4) % 10 = 6 := by
  intro m
  induction m with
  | zero => norm_num
  | succ m ih =>
      rw [show 4 * (m + 1) + 4 = (4 * m + 4) + 4 by omega, pow_add, Nat.mul_mod, ih]
      norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k : ℕ) (h₀ : k = 2008 ^ 2 + 2 ^ 2008) (node_power_cycle : ∀ m : ℕ, 2 ^ (4 * m + 4) % 10 = 6) : k % 4 = 0 ∧ k % 10 = 0 ∧ 0 < k := by
  have hp4 : 2 ^ 2008 % 4 = 0 := by
    rw [show 2008 = 2 * 1004 by decide, pow_mul, Nat.pow_mod]
    norm_num
  have hp10 : 2 ^ 2008 % 10 = 6 := by simpa only [Nat.reduceMul, Nat.reduceAdd] using node_power_cycle 501
  rw [h₀]
  refine ⟨?_, ?_, by positivity⟩
  · rw [Nat.add_mod, hp4]
    norm_num
  · rw [Nat.add_mod, hp10]
    norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k : ℕ) (h₀ : k = 2008 ^ 2 + 2 ^ 2008) (node_power_cycle : ∀ m : ℕ, 2 ^ (4 * m + 4) % 10 = 6) (node_exponent_congruences : k % 4 = 0 ∧ k % 10 = 0 ∧ 0 < k) : (k ^ 2 + 2 ^ k) % 10 = 6 := by
  have hk := node_exponent_congruences
  have he : k = 4 * (k / 4 - 1) + 4 := by omega
  have hp := node_power_cycle (k / 4 - 1)
  rw [← he] at hp
  have hs : k ^ 2 % 10 = 0 := by simp [Nat.pow_mod, hk.2.1]
  rw [Nat.add_mod, hs, hp]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2008_p15 (k : ℕ) (h₀ : k = 2008 ^ 2 + 2 ^ 2008) : (k ^ 2 + 2 ^ k) % 10 = 6 := by
  have node_power_cycle : ∀ m : ℕ, 2 ^ (4 * m + 4) % 10 = 6 := by
    intro m
    induction m with
    | zero => norm_num
    | succ m ih =>
        rw [show 4 * (m + 1) + 4 = (4 * m + 4) + 4 by omega, pow_add, Nat.mul_mod, ih]
        norm_num
  have node_exponent_congruences : k % 4 = 0 ∧ k % 10 = 0 ∧ 0 < k := by
    have hp4 : 2 ^ 2008 % 4 = 0 := by
      rw [show 2008 = 2 * 1004 by decide, pow_mul, Nat.pow_mod]
      norm_num
    have hp10 : 2 ^ 2008 % 10 = 6 := by simpa only [Nat.reduceMul, Nat.reduceAdd] using node_power_cycle 501
    rw [h₀]
    refine ⟨?_, ?_, by positivity⟩
    · rw [Nat.add_mod, hp4]
      norm_num
    · rw [Nat.add_mod, hp10]
      norm_num
  have node_root : (k ^ 2 + 2 ^ k) % 10 = 6 := by
    have hk := node_exponent_congruences
    have he : k = 4 * (k / 4 - 1) + 4 := by omega
    have hp := node_power_cycle (k / 4 - 1)
    rw [← he] at hp
    have hs : k ^ 2 % 10 = 0 := by simp [Nat.pow_mod, hk.2.1]
    rw [Nat.add_mod, hs, hp]
  exact node_root

#print axioms amc12a_2008_p15
