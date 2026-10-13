import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℕ) (t : ℕ → ℕ) (h₀ : t 0 = 0) (h₁ : t 1 = 1)
  (h₂ : ∀ n > 1, t n = t (n - 2) + t (n - 1)) (h₃ : a ≡ 5 [MOD 16]) (h₄ : b ≡ 10 [MOD 16])
  (h₅ : c ≡ 15 [MOD 16]) : ∀ n : ℕ, t (n + 16) = 987 * t (n + 1) + 610 * t n := by
  have recur : ∀ j : ℕ, t (j + 2) = t j + t (j + 1) := by
    intro j
    have h := h₂ (j + 2) (by omega)
    have e0 : j + 2 - 2 = j := by omega
    have e1 : j + 2 - 1 = j + 1 := by omega
    rw [e0, e1] at h
    exact h
  intro n
  have h2 := recur (n + 0)
  have h3 := recur (n + 1)
  have h4 := recur (n + 2)
  have h5 := recur (n + 3)
  have h6 := recur (n + 4)
  have h7 := recur (n + 5)
  have h8 := recur (n + 6)
  have h9 := recur (n + 7)
  have h10 := recur (n + 8)
  have h11 := recur (n + 9)
  have h12 := recur (n + 10)
  have h13 := recur (n + 11)
  have h14 := recur (n + 12)
  have h15 := recur (n + 13)
  have h16 := recur (n + 14)
  norm_num [Nat.add_assoc] at h2 h3 h4 h5 h6 h7 h8 h9 h10 h11 h12 h13 h14 h15 h16
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℕ) (t : ℕ → ℕ) (h₀ : t 0 = 0) (h₁ : t 1 = 1)
  (h₂ : ∀ n > 1, t n = t (n - 2) + t (n - 1)) (h₃ : a ≡ 5 [MOD 16]) (h₄ : b ≡ 10 [MOD 16])
  (h₅ : c ≡ 15 [MOD 16]) (node_sixteen_step_formula : ∀ n : ℕ, t (n + 16) = 987 * t (n + 1) + 610 * t n) : ∀ n : ℕ, t (n + 16) % 7 = t n % 7 := by
  intro n
  rw [node_sixteen_step_formula]
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℕ) (t : ℕ → ℕ) (h₀ : t 0 = 0) (h₁ : t 1 = 1)
  (h₂ : ∀ n > 1, t n = t (n - 2) + t (n - 1)) (h₃ : a ≡ 5 [MOD 16]) (h₄ : b ≡ 10 [MOD 16])
  (h₅ : c ≡ 15 [MOD 16]) (node_modular_period : ∀ n : ℕ, t (n + 16) % 7 = t n % 7) : (t a + t b + t c) % 7 = 5 := by
  have reduce : ∀ n : ℕ, t n % 7 = t (n % 16) % 7 := by
    intro n
    induction n using Nat.strong_induction_on with
    | h n ih =>
      by_cases hn : n < 16
      · rw [Nat.mod_eq_of_lt hn]
      · have hp := node_modular_period (n - 16)
        have he : n - 16 + 16 = n := by omega
        rw [he] at hp
        have hr := ih (n - 16) (by omega)
        have hm : (n - 16) % 16 = n % 16 := by omega
        simpa [hm] using hp.trans hr
  have values : t 5 = 5 ∧ t 10 = 55 ∧ t 15 = 610 := by
    have h2 := h₂ 2 (by omega)
    have h3 := h₂ 3 (by omega)
    have h4 := h₂ 4 (by omega)
    have h5 := h₂ 5 (by omega)
    have h6 := h₂ 6 (by omega)
    have h7 := h₂ 7 (by omega)
    have h8 := h₂ 8 (by omega)
    have h9 := h₂ 9 (by omega)
    have h10 := h₂ 10 (by omega)
    have h11 := h₂ 11 (by omega)
    have h12 := h₂ 12 (by omega)
    have h13 := h₂ 13 (by omega)
    have h14 := h₂ 14 (by omega)
    have h15 := h₂ 15 (by omega)
    norm_num at h2 h3 h4 h5 h6 h7 h8 h9 h10 h11 h12 h13 h14 h15
    omega
  have amod : a % 16 = 5 := by simpa [Nat.ModEq] using h₃
  have bmod : b % 16 = 10 := by simpa [Nat.ModEq] using h₄
  have cmod : c % 16 = 15 := by simpa [Nat.ModEq] using h₅
  have ha := reduce a
  have hb := reduce b
  have hc := reduce c
  rw [amod, values.1] at ha
  rw [bmod, values.2.1] at hb
  rw [cmod, values.2.2] at hc
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_405 (a b c : ℕ) (t : ℕ → ℕ) (h₀ : t 0 = 0) (h₁ : t 1 = 1)
  (h₂ : ∀ n > 1, t n = t (n - 2) + t (n - 1)) (h₃ : a ≡ 5 [MOD 16]) (h₄ : b ≡ 10 [MOD 16])
  (h₅ : c ≡ 15 [MOD 16]) : (t a + t b + t c) % 7 = 5 := by
  have node_sixteen_step_formula : ∀ n : ℕ, t (n + 16) = 987 * t (n + 1) + 610 * t n := by
    have recur : ∀ j : ℕ, t (j + 2) = t j + t (j + 1) := by
      intro j
      have h := h₂ (j + 2) (by omega)
      have e0 : j + 2 - 2 = j := by omega
      have e1 : j + 2 - 1 = j + 1 := by omega
      rw [e0, e1] at h
      exact h
    intro n
    have h2 := recur (n + 0)
    have h3 := recur (n + 1)
    have h4 := recur (n + 2)
    have h5 := recur (n + 3)
    have h6 := recur (n + 4)
    have h7 := recur (n + 5)
    have h8 := recur (n + 6)
    have h9 := recur (n + 7)
    have h10 := recur (n + 8)
    have h11 := recur (n + 9)
    have h12 := recur (n + 10)
    have h13 := recur (n + 11)
    have h14 := recur (n + 12)
    have h15 := recur (n + 13)
    have h16 := recur (n + 14)
    norm_num [Nat.add_assoc] at h2 h3 h4 h5 h6 h7 h8 h9 h10 h11 h12 h13 h14 h15 h16
    omega
  have node_modular_period : ∀ n : ℕ, t (n + 16) % 7 = t n % 7 := by
    intro n
    rw [node_sixteen_step_formula]
    omega
  have node_root : (t a + t b + t c) % 7 = 5 := by
    have reduce : ∀ n : ℕ, t n % 7 = t (n % 16) % 7 := by
      intro n
      induction n using Nat.strong_induction_on with
      | h n ih =>
        by_cases hn : n < 16
        · rw [Nat.mod_eq_of_lt hn]
        · have hp := node_modular_period (n - 16)
          have he : n - 16 + 16 = n := by omega
          rw [he] at hp
          have hr := ih (n - 16) (by omega)
          have hm : (n - 16) % 16 = n % 16 := by omega
          simpa [hm] using hp.trans hr
    have values : t 5 = 5 ∧ t 10 = 55 ∧ t 15 = 610 := by
      have h2 := h₂ 2 (by omega)
      have h3 := h₂ 3 (by omega)
      have h4 := h₂ 4 (by omega)
      have h5 := h₂ 5 (by omega)
      have h6 := h₂ 6 (by omega)
      have h7 := h₂ 7 (by omega)
      have h8 := h₂ 8 (by omega)
      have h9 := h₂ 9 (by omega)
      have h10 := h₂ 10 (by omega)
      have h11 := h₂ 11 (by omega)
      have h12 := h₂ 12 (by omega)
      have h13 := h₂ 13 (by omega)
      have h14 := h₂ 14 (by omega)
      have h15 := h₂ 15 (by omega)
      norm_num at h2 h3 h4 h5 h6 h7 h8 h9 h10 h11 h12 h13 h14 h15
      omega
    have amod : a % 16 = 5 := by simpa [Nat.ModEq] using h₃
    have bmod : b % 16 = 10 := by simpa [Nat.ModEq] using h₄
    have cmod : c % 16 = 15 := by simpa [Nat.ModEq] using h₅
    have ha := reduce a
    have hb := reduce b
    have hc := reduce c
    rw [amod, values.1] at ha
    rw [bmod, values.2.1] at hb
    rw [cmod, values.2.2] at hc
    omega
  exact node_root

#print axioms mathd_numbertheory_405
