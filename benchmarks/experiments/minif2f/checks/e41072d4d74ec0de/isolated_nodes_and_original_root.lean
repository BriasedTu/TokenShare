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
  (h₀ : 0 < n)
  (h₁ : Nat.Prime (2^n - 1)) : ∀ m : ℕ, m ∣ n → 2 ^ m - 1 ∣ 2 ^ n - 1 := by
  intro m hm
  obtain ⟨k, hk⟩ := hm
  rw [hk, pow_mul]
  simpa using Nat.sub_dvd_pow_sub_pow (2 ^ m) 1 k

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ)
  (h₀ : 0 < n)
  (h₁ : Nat.Prime (2^n - 1)) : ∀ m : ℕ, 2 ≤ m → m < n → 1 < 2 ^ m - 1 ∧ 2 ^ m - 1 < 2 ^ n - 1 := by
  intro m hm hmn
  have lo : 2 ^ 2 ≤ 2 ^ m := Nat.pow_le_pow_right (by decide) hm
  have hi : 2 ^ m < 2 ^ n := Nat.pow_lt_pow_right (by decide) hmn
  norm_num at lo
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ)
  (h₀ : 0 < n)
  (h₁ : Nat.Prime (2^n - 1)) (node_exponent_divisibility : ∀ m : ℕ, m ∣ n → 2 ^ m - 1 ∣ 2 ^ n - 1) (node_proper_divisor_bounds : ∀ m : ℕ, 2 ≤ m → m < n → 1 < 2 ^ m - 1 ∧ 2 ^ m - 1 < 2 ^ n - 1) : Nat.Prime n := by
  have hn : 2 ≤ n := by
    by_contra h
    have : n = 1 := by omega
    norm_num [this] at h₁
  by_contra hp
  obtain ⟨m, hmd, hm, hmn⟩ := Nat.exists_dvd_of_not_prime2 hn hp
  have bounds := node_proper_divisor_bounds m hm hmn
  have factors := h₁.eq_one_or_self_of_dvd (2 ^ m - 1) (node_exponent_divisibility m hmd)
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_2pownm1prime_nprime (n : ℕ)
  (h₀ : 0 < n)
  (h₁ : Nat.Prime (2^n - 1)) : Nat.Prime n := by
  have node_exponent_divisibility : ∀ m : ℕ, m ∣ n → 2 ^ m - 1 ∣ 2 ^ n - 1 := by
    intro m hm
    obtain ⟨k, hk⟩ := hm
    rw [hk, pow_mul]
    simpa using Nat.sub_dvd_pow_sub_pow (2 ^ m) 1 k
  have node_proper_divisor_bounds : ∀ m : ℕ, 2 ≤ m → m < n → 1 < 2 ^ m - 1 ∧ 2 ^ m - 1 < 2 ^ n - 1 := by
    intro m hm hmn
    have lo : 2 ^ 2 ≤ 2 ^ m := Nat.pow_le_pow_right (by decide) hm
    have hi : 2 ^ m < 2 ^ n := Nat.pow_lt_pow_right (by decide) hmn
    norm_num at lo
    omega
  have node_root : Nat.Prime n := by
    have hn : 2 ≤ n := by
      by_contra h
      have : n = 1 := by omega
      norm_num [this] at h₁
    by_contra hp
    obtain ⟨m, hmd, hm, hmn⟩ := Nat.exists_dvd_of_not_prime2 hn hp
    have bounds := node_proper_divisor_bounds m hm hmn
    have factors := h₁.eq_one_or_self_of_dvd (2 ^ m - 1) (node_exponent_divisibility m hmd)
    omega
  exact node_root

#print axioms numbertheory_2pownm1prime_nprime
