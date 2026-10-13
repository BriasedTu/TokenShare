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
  (h₀ : 0 < n) : ∀ k x q : ℕ, 2 ≤ k → x = 1 + 2 ^ k + q * 2 ^ (k + 1) → ∃ r : ℕ, x ^ 2 = 1 + 2 ^ (k + 1) + r * 2 ^ (k + 2) := by
  intro k x q hk hx
  have he : k = k - 2 + 2 := by omega
  have hpow : 2 ^ k = 4 * 2 ^ (k - 2) := by
    nth_rw 1 [he]
    rw [pow_add]
    ring
  refine ⟨q + 2 ^ (k - 2) * (1 + 4 * q + 4 * q ^ 2), ?_⟩
  rw [hx]
  simp only [pow_succ]
  rw [hpow]
  ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ)
  (h₀ : 0 < n) (node_binary_squaring_lift : ∀ k x q : ℕ, 2 ≤ k → x = 1 + 2 ^ k + q * 2 ^ (k + 1) → ∃ r : ℕ, x ^ 2 = 1 + 2 ^ (k + 1) + r * 2 ^ (k + 2)) : (3^(2^n) - 1) % (2^(n + 3)) = 2^(n + 2) := by
  have representation : ∀ j : ℕ, ∃ q : ℕ, 3 ^ (2 ^ (j + 1)) = 1 + 2 ^ (j + 3) + q * 2 ^ (j + 4) := by
    intro j
    induction j with
    | zero => exact ⟨0, by norm_num⟩
    | succ j ih =>
      obtain ⟨q, hq⟩ := ih
      obtain ⟨r, hr⟩ := node_binary_squaring_lift (j + 3) (3 ^ (2 ^ (j + 1))) q (by omega) hq
      refine ⟨r, ?_⟩
      have he : 3 ^ (2 ^ (j + 1 + 1)) = (3 ^ (2 ^ (j + 1))) ^ 2 := by
        rw [pow_succ, pow_mul]
      rw [he]
      exact hr
  cases n with
  | zero => omega
  | succ j =>
    obtain ⟨q, hq⟩ := representation j
    have hs : 3 ^ (2 ^ (j + 1)) - 1 = 2 ^ (j + 3) + q * 2 ^ (j + 4) := by simpa [Nat.add_assoc] using congrArg (fun z : ℕ => z - 1) hq
    change (3 ^ (2 ^ (j + 1)) - 1) % 2 ^ (j + 4) = 2 ^ (j + 3)
    rw [hs]
    have hlt : (2 : ℕ) ^ (j + 3) < 2 ^ (j + 4) := Nat.pow_lt_pow_right (by decide) (by omega)
    simpa using Nat.mod_eq_of_lt hlt

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_3pow2pownm1mod2pownp3eq2pownp2 (n : ℕ)
  (h₀ : 0 < n) : (3^(2^n) - 1) % (2^(n + 3)) = 2^(n + 2) := by
  have node_binary_squaring_lift : ∀ k x q : ℕ, 2 ≤ k → x = 1 + 2 ^ k + q * 2 ^ (k + 1) → ∃ r : ℕ, x ^ 2 = 1 + 2 ^ (k + 1) + r * 2 ^ (k + 2) := by
    intro k x q hk hx
    have he : k = k - 2 + 2 := by omega
    have hpow : 2 ^ k = 4 * 2 ^ (k - 2) := by
      nth_rw 1 [he]
      rw [pow_add]
      ring
    refine ⟨q + 2 ^ (k - 2) * (1 + 4 * q + 4 * q ^ 2), ?_⟩
    rw [hx]
    simp only [pow_succ]
    rw [hpow]
    ring
  have node_root : (3^(2^n) - 1) % (2^(n + 3)) = 2^(n + 2) := by
    have representation : ∀ j : ℕ, ∃ q : ℕ, 3 ^ (2 ^ (j + 1)) = 1 + 2 ^ (j + 3) + q * 2 ^ (j + 4) := by
      intro j
      induction j with
      | zero => exact ⟨0, by norm_num⟩
      | succ j ih =>
        obtain ⟨q, hq⟩ := ih
        obtain ⟨r, hr⟩ := node_binary_squaring_lift (j + 3) (3 ^ (2 ^ (j + 1))) q (by omega) hq
        refine ⟨r, ?_⟩
        have he : 3 ^ (2 ^ (j + 1 + 1)) = (3 ^ (2 ^ (j + 1))) ^ 2 := by
          rw [pow_succ, pow_mul]
        rw [he]
        exact hr
    cases n with
    | zero => omega
    | succ j =>
      obtain ⟨q, hq⟩ := representation j
      have hs : 3 ^ (2 ^ (j + 1)) - 1 = 2 ^ (j + 3) + q * 2 ^ (j + 4) := by simpa [Nat.add_assoc] using congrArg (fun z : ℕ => z - 1) hq
      change (3 ^ (2 ^ (j + 1)) - 1) % 2 ^ (j + 4) = 2 ^ (j + 3)
      rw [hs]
      have hlt : (2 : ℕ) ^ (j + 3) < 2 ^ (j + 4) := Nat.pow_lt_pow_right (by decide) (by omega)
      simpa using Nat.mod_eq_of_lt hlt
  exact node_root

#print axioms numbertheory_3pow2pownm1mod2pownp3eq2pownp2
