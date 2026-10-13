import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (m n : ℕ)
  (f : ℕ → ℕ)
  (h₀ : ∀ x, f x = 4^x + 6^x + 9^x)
  (h₁ : 0 < m ∧ 0 < n)
  (h₂ : m ≤ n) : ∀ k : ℕ, f (2 * k) = f k * (4 ^ k + 9 ^ k - 6 ^ k) := by
  intro k
  have hb : 6 ^ k ≤ 4 ^ k + 9 ^ k := by
    have h : 6 ^ k ≤ 9 ^ k := Nat.pow_le_pow_left (by decide) k
    exact h.trans (Nat.le_add_left _ _)
  have he := Nat.sub_add_cancel hb
  have he' := congrArg (fun z : ℕ => z * (4 ^ k + 6 ^ k + 9 ^ k)) he
  have hc : 4 ^ k * 9 ^ k = (6 ^ k) ^ 2 := by
    rw [pow_two, ← mul_pow, ← mul_pow]
    norm_num
  have p4 : 4 ^ (2 * k) = (4 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
  have p6 : 6 ^ (2 * k) = (6 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
  have p9 : 9 ^ (2 * k) = (9 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
  rw [h₀, h₀, p4, p6, p9]
  nlinarith only [he', hc]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (m n : ℕ)
  (f : ℕ → ℕ)
  (h₀ : ∀ x, f x = 4^x + 6^x + 9^x)
  (h₁ : 0 < m ∧ 0 < n)
  (h₂ : m ≤ n) (node_doubling_factorization : ∀ k : ℕ, f (2 * k) = f k * (4 ^ k + 9 ^ k - 6 ^ k)) : f (2^m)∣f (2^n) := by
  have step : ∀ k : ℕ, f k ∣ f (2 * k) := by
    intro k
    exact ⟨4 ^ k + 9 ^ k - 6 ^ k, node_doubling_factorization k⟩
  have iteration : ∀ r : ℕ, m ≤ r → f (2 ^ m) ∣ f (2 ^ r) := by
    intro r hr
    induction r, hr using Nat.le_induction with
    | base => exact dvd_rfl
    | succ r hr ih =>
      have hs := step (2 ^ r)
      have hs' : f (2 ^ r) ∣ f (2 ^ (r + 1)) := by simpa [pow_succ, Nat.mul_comm] using hs
      exact ih.trans hs'
  exact iteration n h₂

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown (m n : ℕ)
  (f : ℕ → ℕ)
  (h₀ : ∀ x, f x = 4^x + 6^x + 9^x)
  (h₁ : 0 < m ∧ 0 < n)
  (h₂ : m ≤ n) : f (2^m)∣f (2^n) := by
  have node_doubling_factorization : ∀ k : ℕ, f (2 * k) = f k * (4 ^ k + 9 ^ k - 6 ^ k) := by
    intro k
    have hb : 6 ^ k ≤ 4 ^ k + 9 ^ k := by
      have h : 6 ^ k ≤ 9 ^ k := Nat.pow_le_pow_left (by decide) k
      exact h.trans (Nat.le_add_left _ _)
    have he := Nat.sub_add_cancel hb
    have he' := congrArg (fun z : ℕ => z * (4 ^ k + 6 ^ k + 9 ^ k)) he
    have hc : 4 ^ k * 9 ^ k = (6 ^ k) ^ 2 := by
      rw [pow_two, ← mul_pow, ← mul_pow]
      norm_num
    have p4 : 4 ^ (2 * k) = (4 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
    have p6 : 6 ^ (2 * k) = (6 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
    have p9 : 9 ^ (2 * k) = (9 ^ k) ^ 2 := by rw [Nat.mul_comm 2 k, pow_mul]
    rw [h₀, h₀, p4, p6, p9]
    nlinarith only [he', hc]
  have node_root : f (2^m)∣f (2^n) := by
    have step : ∀ k : ℕ, f k ∣ f (2 * k) := by
      intro k
      exact ⟨4 ^ k + 9 ^ k - 6 ^ k, node_doubling_factorization k⟩
    have iteration : ∀ r : ℕ, m ≤ r → f (2 ^ m) ∣ f (2 ^ r) := by
      intro r hr
      induction r, hr using Nat.le_induction with
      | base => exact dvd_rfl
      | succ r hr ih =>
        have hs := step (2 ^ r)
        have hs' : f (2 ^ r) ∣ f (2 ^ (r + 1)) := by simpa [pow_succ, Nat.mul_comm] using hs
        exact ih.trans hs'
    exact iteration n h₂
  exact node_root

#print axioms numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown
