import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Set ℕ) (u v : ℕ) (h₀ : ∀ a : ℕ, a ∈ S ↔ 0 < a ∧ 27 * a % 40 = 17)
    (h₁ : IsLeast S u) (h₂ : IsLeast (S \ {u}) v) : ∀ n : ℕ, n ∈ S → n % 40 = 11 := by
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
example (S : Set ℕ) (u v : ℕ) (h₀ : ∀ a : ℕ, a ∈ S ↔ 0 < a ∧ 27 * a % 40 = 17)
    (h₁ : IsLeast S u) (h₂ : IsLeast (S \ {u}) v) (node_solution_residue : ∀ n : ℕ, n ∈ S → n % 40 = 11) : u + v = 62 := by
  have hfirst : 11 ∈ S := by rw [h₀]; norm_num
  have huhi := h₁.2 hfirst
  have hum := node_solution_residue u h₁.1
  have hu : u = 11 := by omega
  have hsecond : 51 ∈ S \ {u} := by
    simp only [Set.mem_diff, Set.mem_singleton_iff]
    constructor
    · rw [h₀]; norm_num
    · omega
  have hvhi := h₂.2 hsecond
  have hvm := node_solution_residue v h₂.1.1
  have hvne : v ≠ u := by simpa using h₂.1.2
  have hv : v = 51 := by omega
  norm_num [hu, hv]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_42 (S : Set ℕ) (u v : ℕ) (h₀ : ∀ a : ℕ, a ∈ S ↔ 0 < a ∧ 27 * a % 40 = 17)
    (h₁ : IsLeast S u) (h₂ : IsLeast (S \ {u}) v) : u + v = 62 := by
  have node_solution_residue : ∀ n : ℕ, n ∈ S → n % 40 = 11 := by
    intro n hn
    have hm := (h₀ n).mp hn
    omega
  have node_root : u + v = 62 := by
    have hfirst : 11 ∈ S := by rw [h₀]; norm_num
    have huhi := h₁.2 hfirst
    have hum := node_solution_residue u h₁.1
    have hu : u = 11 := by omega
    have hsecond : 51 ∈ S \ {u} := by
      simp only [Set.mem_diff, Set.mem_singleton_iff]
      constructor
      · rw [h₀]; norm_num
      · omega
    have hvhi := h₂.2 hsecond
    have hvm := node_solution_residue v h₂.1.1
    have hvne : v ≠ u := by simpa using h₂.1.2
    have hv : v = 51 := by omega
    norm_num [hu, hv]
  exact node_root

#print axioms mathd_numbertheory_42
