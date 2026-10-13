import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ) (h₀ : Nat.Prime a) (h₁ : Nat.Prime b) (h₂ : Nat.Prime (a + b))
  (h₃ : Nat.Prime (a - b)) : b = 2 := by
  have ha := h₀.two_le
  have hb := h₁.two_le
  have hd := h₃.two_le
  rcases h₀.eq_two_or_odd with h | h
  · omega
  rcases h₁.eq_two_or_odd with hb2 | hbo
  · exact hb2
  rcases h₂.eq_two_or_odd with hs2 | hso <;> omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ) (h₀ : Nat.Prime a) (h₁ : Nat.Prime b) (h₂ : Nat.Prime (a + b))
  (h₃ : Nat.Prime (a - b)) (node_even_prime : b = 2) : a = 5 := by
  have ha := h₀.two_le
  have hd := h₃.two_le
  have hs : Nat.Prime (a + 2) := by simpa [node_even_prime] using h₂
  have hm : Nat.Prime (a - 2) := by simpa [node_even_prime] using h₃
  have hm2 := hm.two_le
  have residues : a % 3 = 0 ∨ a % 3 = 1 ∨ a % 3 = 2 := by omega
  rcases residues with h | h | h
  · have d : 3 ∣ a := Nat.dvd_of_mod_eq_zero h
    have hp := h₀.eq_one_or_self_of_dvd 3 d
    omega
  · have d : 3 ∣ a + 2 := by omega
    have hp := hs.eq_one_or_self_of_dvd 3 d
    omega
  · have d : 3 ∣ a - 2 := by omega
    have hp := hm.eq_one_or_self_of_dvd 3 d
    omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ) (h₀ : Nat.Prime a) (h₁ : Nat.Prime b) (h₂ : Nat.Prime (a + b))
  (h₃ : Nat.Prime (a - b)) (node_even_prime : b = 2) (node_modulo_three_restriction : a = 5) : Nat.Prime (a + b + (a - b + (a + b))) := by
  norm_num [node_even_prime, node_modulo_three_restriction]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12b_2002_p11 (a b : ℕ) (h₀ : Nat.Prime a) (h₁ : Nat.Prime b) (h₂ : Nat.Prime (a + b))
  (h₃ : Nat.Prime (a - b)) : Nat.Prime (a + b + (a - b + (a + b))) := by
  have node_even_prime : b = 2 := by
    have ha := h₀.two_le
    have hb := h₁.two_le
    have hd := h₃.two_le
    rcases h₀.eq_two_or_odd with h | h
    · omega
    rcases h₁.eq_two_or_odd with hb2 | hbo
    · exact hb2
    rcases h₂.eq_two_or_odd with hs2 | hso <;> omega
  have node_modulo_three_restriction : a = 5 := by
    have ha := h₀.two_le
    have hd := h₃.two_le
    have hs : Nat.Prime (a + 2) := by simpa [node_even_prime] using h₂
    have hm : Nat.Prime (a - 2) := by simpa [node_even_prime] using h₃
    have hm2 := hm.two_le
    have residues : a % 3 = 0 ∨ a % 3 = 1 ∨ a % 3 = 2 := by omega
    rcases residues with h | h | h
    · have d : 3 ∣ a := Nat.dvd_of_mod_eq_zero h
      have hp := h₀.eq_one_or_self_of_dvd 3 d
      omega
    · have d : 3 ∣ a + 2 := by omega
      have hp := hs.eq_one_or_self_of_dvd 3 d
      omega
    · have d : 3 ∣ a - 2 := by omega
      have hp := hm.eq_one_or_self_of_dvd 3 d
      omega
  have node_root : Nat.Prime (a + b + (a - b + (a + b))) := by
    norm_num [node_even_prime, node_modulo_three_restriction]
  exact node_root

#print axioms amc12b_2002_p11
