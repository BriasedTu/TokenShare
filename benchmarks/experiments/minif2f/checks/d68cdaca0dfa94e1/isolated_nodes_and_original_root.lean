import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : a % 10 = 2)
  (h₂ : b % 10 = 4)
  (h₃ : Nat.gcd a b = 6) : (a = 12 ∨ 42 ≤ a) ∧ 24 ≤ b := by
  have da : 6 ∣ a := by simpa [h₃] using Nat.gcd_dvd_left a b
  have db : 6 ∣ b := by simpa [h₃] using Nat.gcd_dvd_right a b
  have ma := Nat.mod_eq_zero_of_dvd da
  have mb := Nat.mod_eq_zero_of_dvd db
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : a % 10 = 2)
  (h₂ : b % 10 = 4)
  (h₃ : Nat.gcd a b = 6) : a = 12 → 54 ≤ b := by
  intro ha
  by_contra hb
  have db : 6 ∣ b := by simpa [h₃] using Nat.gcd_dvd_right a b
  have mb := Nat.mod_eq_zero_of_dvd db
  have beq : b = 24 := by omega
  norm_num [ha, beq] at h₃

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : a % 10 = 2)
  (h₂ : b % 10 = 4)
  (h₃ : Nat.gcd a b = 6) (node_residue_bounds : (a = 12 ∨ 42 ≤ a) ∧ 24 ≤ b) (node_exceptional_gcd : a = 12 → 54 ≤ b) : 108 ≤ Nat.lcm a b := by
  have prod : 648 ≤ a * b := by
    rcases node_residue_bounds with ⟨ha, hb⟩
    rcases ha with ha | ha
    · have hb' := node_exceptional_gcd ha
      nlinarith
    · nlinarith
  have ident := Nat.gcd_mul_lcm a b
  rw [h₃] at ident
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_495 (a b : ℕ)
  (h₀ : 0 < a ∧ 0 < b)
  (h₁ : a % 10 = 2)
  (h₂ : b % 10 = 4)
  (h₃ : Nat.gcd a b = 6) : 108 ≤ Nat.lcm a b := by
  have node_residue_bounds : (a = 12 ∨ 42 ≤ a) ∧ 24 ≤ b := by
    have da : 6 ∣ a := by simpa [h₃] using Nat.gcd_dvd_left a b
    have db : 6 ∣ b := by simpa [h₃] using Nat.gcd_dvd_right a b
    have ma := Nat.mod_eq_zero_of_dvd da
    have mb := Nat.mod_eq_zero_of_dvd db
    omega
  have node_exceptional_gcd : a = 12 → 54 ≤ b := by
    intro ha
    by_contra hb
    have db : 6 ∣ b := by simpa [h₃] using Nat.gcd_dvd_right a b
    have mb := Nat.mod_eq_zero_of_dvd db
    have beq : b = 24 := by omega
    norm_num [ha, beq] at h₃
  have node_root : 108 ≤ Nat.lcm a b := by
    have prod : 648 ≤ a * b := by
      rcases node_residue_bounds with ⟨ha, hb⟩
      rcases ha with ha | ha
      · have hb' := node_exceptional_gcd ha
        nlinarith
      · nlinarith
    have ident := Nat.gcd_mul_lcm a b
    rw [h₃] at ident
    omega
  exact node_root

#print axioms mathd_numbertheory_495
