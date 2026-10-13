import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (u v : ℕ) (S : Set ℕ)
  (h₀ : ∀ n : ℕ, n ∈ S ↔ 0 < n ∧ 14 * n % 100 = 46) (h₁ : IsLeast S u)
  (h₂ : IsLeast (S \ {u}) v) : ∀ n : ℕ, n ∈ S → n % 50 = 39 := by
  intro n hn
  have hm := (h₀ n).mp hn
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (u v : ℕ) (S : Set ℕ)
  (h₀ : ∀ n : ℕ, n ∈ S ↔ 0 < n ∧ 14 * n % 100 = 46) (h₁ : IsLeast S u)
  (h₂ : IsLeast (S \ {u}) v) (node_solution_residue : ∀ n : ℕ, n ∈ S → n % 50 = 39) : (u + v : ℚ) / 2 = 64 := by
  have hfirst : 39 ∈ S := by rw [h₀]; norm_num
  have huhi := h₁.2 hfirst
  have hum := node_solution_residue u h₁.1
  have hu : u = 39 := by omega
  have hsecond : 89 ∈ S \ {u} := by
    simp only [Set.mem_diff, Set.mem_singleton_iff]
    constructor
    · rw [h₀]; norm_num
    · omega
  have hvhi := h₂.2 hsecond
  have hvm := node_solution_residue v h₂.1.1
  have hvne : v ≠ u := by simpa using h₂.1.2
  have hv : v = 89 := by omega
  norm_num [hu, hv]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_13 (u v : ℕ) (S : Set ℕ)
  (h₀ : ∀ n : ℕ, n ∈ S ↔ 0 < n ∧ 14 * n % 100 = 46) (h₁ : IsLeast S u)
  (h₂ : IsLeast (S \ {u}) v) : (u + v : ℚ) / 2 = 64 := by
  have node_solution_residue : ∀ n : ℕ, n ∈ S → n % 50 = 39 := by
    intro n hn
    have hm := (h₀ n).mp hn
    omega
  have node_root : (u + v : ℚ) / 2 = 64 := by
    have hfirst : 39 ∈ S := by rw [h₀]; norm_num
    have huhi := h₁.2 hfirst
    have hum := node_solution_residue u h₁.1
    have hu : u = 39 := by omega
    have hsecond : 89 ∈ S \ {u} := by
      simp only [Set.mem_diff, Set.mem_singleton_iff]
      constructor
      · rw [h₀]; norm_num
      · omega
    have hvhi := h₂.2 hsecond
    have hvm := node_solution_residue v h₂.1.1
    have hvne : v ≠ u := by simpa using h₂.1.2
    have hv : v = 89 := by omega
    norm_num [hu, hv]
  exact node_root

#print axioms mathd_numbertheory_13
