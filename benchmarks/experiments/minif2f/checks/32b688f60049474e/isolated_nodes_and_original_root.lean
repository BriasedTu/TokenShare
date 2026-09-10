import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (d : ℕ → ℕ)
  (h₀ : d 0 = 0)
  (h₁ : d 1 = 0)
  (h₂ : d 2 = 1)
  (h₃ : ∀ n≥3, d n = d (n - 1) + d (n - 3)) : ∀ n : ℕ, d (n + 7) % 2 = d n % 2 := by
  intro n
  have ha : d (n + 3) = d (n + 2) + d n := by
    simpa only [show n + 3 - 1 = n + 2 by omega, show n + 3 - 3 = n by omega] using h₃ (n + 3) (by omega)
  have hb : d (n + 4) = d (n + 3) + d (n + 1) := by
    simpa only [show n + 4 - 1 = n + 3 by omega, show n + 4 - 3 = n + 1 by omega] using h₃ (n + 4) (by omega)
  have hc : d (n + 5) = d (n + 4) + d (n + 2) := by
    simpa only [show n + 5 - 1 = n + 4 by omega, show n + 5 - 3 = n + 2 by omega] using h₃ (n + 5) (by omega)
  have hd : d (n + 6) = d (n + 5) + d (n + 3) := by
    simpa only [show n + 6 - 1 = n + 5 by omega, show n + 6 - 3 = n + 3 by omega] using h₃ (n + 6) (by omega)
  have he : d (n + 7) = d (n + 6) + d (n + 4) := by
    simpa only [show n + 7 - 1 = n + 6 by omega, show n + 7 - 3 = n + 4 by omega] using h₃ (n + 7) (by omega)
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (d : ℕ → ℕ)
  (h₀ : d 0 = 0)
  (h₁ : d 1 = 0)
  (h₂ : d 2 = 1)
  (h₃ : ∀ n≥3, d n = d (n - 1) + d (n - 3)) (node_seven_step_parity : ∀ n : ℕ, d (n + 7) % 2 = d n % 2) : ∀ n : ℕ, d n % 2 = d (n % 7) % 2 := by
  intro n
  induction n using Nat.strong_induction_on with
  | h n ih =>
    by_cases hn : n < 7
    · rw [Nat.mod_eq_of_lt hn]
    · have hn7 : 7 ≤ n := by omega
      have hp := node_seven_step_parity (n - 7)
      have hi := ih (n - 7) (by omega)
      rw [Nat.sub_add_cancel hn7] at hp
      rw [hp, hi, ← Nat.mod_eq_sub_mod hn7]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (d : ℕ → ℕ)
  (h₀ : d 0 = 0)
  (h₁ : d 1 = 0)
  (h₂ : d 2 = 1)
  (h₃ : ∀ n≥3, d n = d (n - 1) + d (n - 3)) (node_residue_reduction : ∀ n : ℕ, d n % 2 = d (n % 7) % 2) : Even (d 2021) ∧ Odd (d 2022) ∧ Even (d 2023) := by
  have hd3 := h₃ 3 (by omega)
  norm_num [h₀, h₂] at hd3
  have hd4 := h₃ 4 (by omega)
  norm_num [h₁, hd3] at hd4
  have hd5 := h₃ 5 (by omega)
  norm_num [h₂, hd4] at hd5
  have hd6 := h₃ 6 (by omega)
  norm_num [hd3, hd5] at hd6
  have ha := node_residue_reduction 2021
  have hb := node_residue_reduction 2022
  have hc := node_residue_reduction 2023
  norm_num [hd5, hd6, h₀] at ha hb hc
  exact ⟨Nat.even_iff.mpr ha, Nat.odd_iff.mpr hb, Nat.even_iff.mpr hc⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2021_p8 (d : ℕ → ℕ)
  (h₀ : d 0 = 0)
  (h₁ : d 1 = 0)
  (h₂ : d 2 = 1)
  (h₃ : ∀ n≥3, d n = d (n - 1) + d (n - 3)) : Even (d 2021) ∧ Odd (d 2022) ∧ Even (d 2023) := by
  have node_seven_step_parity : ∀ n : ℕ, d (n + 7) % 2 = d n % 2 := by
    intro n
    have ha : d (n + 3) = d (n + 2) + d n := by
      simpa only [show n + 3 - 1 = n + 2 by omega, show n + 3 - 3 = n by omega] using h₃ (n + 3) (by omega)
    have hb : d (n + 4) = d (n + 3) + d (n + 1) := by
      simpa only [show n + 4 - 1 = n + 3 by omega, show n + 4 - 3 = n + 1 by omega] using h₃ (n + 4) (by omega)
    have hc : d (n + 5) = d (n + 4) + d (n + 2) := by
      simpa only [show n + 5 - 1 = n + 4 by omega, show n + 5 - 3 = n + 2 by omega] using h₃ (n + 5) (by omega)
    have hd : d (n + 6) = d (n + 5) + d (n + 3) := by
      simpa only [show n + 6 - 1 = n + 5 by omega, show n + 6 - 3 = n + 3 by omega] using h₃ (n + 6) (by omega)
    have he : d (n + 7) = d (n + 6) + d (n + 4) := by
      simpa only [show n + 7 - 1 = n + 6 by omega, show n + 7 - 3 = n + 4 by omega] using h₃ (n + 7) (by omega)
    omega
  have node_residue_reduction : ∀ n : ℕ, d n % 2 = d (n % 7) % 2 := by
    intro n
    induction n using Nat.strong_induction_on with
    | h n ih =>
      by_cases hn : n < 7
      · rw [Nat.mod_eq_of_lt hn]
      · have hn7 : 7 ≤ n := by omega
        have hp := node_seven_step_parity (n - 7)
        have hi := ih (n - 7) (by omega)
        rw [Nat.sub_add_cancel hn7] at hp
        rw [hp, hi, ← Nat.mod_eq_sub_mod hn7]
  have node_root : Even (d 2021) ∧ Odd (d 2022) ∧ Even (d 2023) := by
    have hd3 := h₃ 3 (by omega)
    norm_num [h₀, h₂] at hd3
    have hd4 := h₃ 4 (by omega)
    norm_num [h₁, hd3] at hd4
    have hd5 := h₃ 5 (by omega)
    norm_num [h₂, hd4] at hd5
    have hd6 := h₃ 6 (by omega)
    norm_num [hd3, hd5] at hd6
    have ha := node_residue_reduction 2021
    have hb := node_residue_reduction 2022
    have hc := node_residue_reduction 2023
    norm_num [hd5, hd6, h₀] at ha hb hc
    exact ⟨Nat.even_iff.mpr ha, Nat.odd_iff.mpr hb, Nat.even_iff.mpr hc⟩
  exact node_root

#print axioms amc12a_2021_p8
