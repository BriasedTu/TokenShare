import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (p a : ℕ)
  (h₀ : 0 < a)
  (h₁ : Nat.Prime p) : ∀ n : ℕ, (n + 1) ^ p ≡ n ^ p + 1 [MOD p] := by
  letI : Fact p.Prime := ⟨h₁⟩
  intro n
  apply (ZMod.natCast_eq_natCast_iff _ _ p).mp
  push_cast
  simp [add_pow_char]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (p a : ℕ)
  (h₀ : 0 < a)
  (h₁ : Nat.Prime p) (node_frobenius_successor_step : ∀ n : ℕ, (n + 1) ^ p ≡ n ^ p + 1 [MOD p]) : p ∣ (a^p - a) := by
  have hmod : ∀ n : ℕ, n ^ p ≡ n [MOD p] := by
    intro n
    induction n with
    | zero => simp [Nat.ModEq, h₁.ne_zero]
    | succ n ih => exact (node_frobenius_successor_step n).trans (ih.add (Nat.ModEq.refl 1))
  exact (Nat.modEq_iff_dvd' (Nat.le_self_pow h₁.ne_zero a)).mp (hmod a).symm

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem induction_pprime_pdvdapowpma (p a : ℕ)
  (h₀ : 0 < a)
  (h₁ : Nat.Prime p) : p ∣ (a^p - a) := by
  have node_frobenius_successor_step : ∀ n : ℕ, (n + 1) ^ p ≡ n ^ p + 1 [MOD p] := by
    letI : Fact p.Prime := ⟨h₁⟩
    intro n
    apply (ZMod.natCast_eq_natCast_iff _ _ p).mp
    push_cast
    simp [add_pow_char]
  have node_root : p ∣ (a^p - a) := by
    have hmod : ∀ n : ℕ, n ^ p ≡ n [MOD p] := by
      intro n
      induction n with
      | zero => simp [Nat.ModEq, h₁.ne_zero]
      | succ n ih => exact (node_frobenius_successor_step n).trans (ih.add (Nat.ModEq.refl 1))
    exact (Nat.modEq_iff_dvd' (Nat.le_self_pow h₁.ne_zero a)).mp (hmod a).symm
  exact node_root

#print axioms induction_pprime_pdvdapowpma
