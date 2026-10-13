import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ → ℕ)
  (g : ℕ → ℕ)
  (h₀ : ∀ y, f 0 y = y + 1)
  (h₁ : ∀ x, f (x + 1) 0 = f x 1)
  (h₂ : ∀ x y, f (x + 1) (y + 1) = f x (f (x + 1) y))
  (h₃ : g 0 = 2)
  (h₄ : ∀ n, g (n + 1) = 2^(g n)) : (∀ y : ℕ, f 1 y = y + 2) ∧ (∀ y : ℕ, f 2 y = 2 * y + 3) := by
  have first : ∀ y : ℕ, f 1 y = y + 2 := by
    intro y
    induction y with
    | zero => simpa [h₀] using h₁ 0
    | succ y ih =>
      have h := h₂ 0 y
      norm_num [h₀] at h
      omega
  refine ⟨first, ?_⟩
  intro y
  induction y with
  | zero => simpa [first] using h₁ 1
  | succ y ih =>
    have h := h₂ 1 y
    norm_num [first] at h
    omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ → ℕ)
  (g : ℕ → ℕ)
  (h₀ : ∀ y, f 0 y = y + 1)
  (h₁ : ∀ x, f (x + 1) 0 = f x 1)
  (h₂ : ∀ x y, f (x + 1) (y + 1) = f x (f (x + 1) y))
  (h₃ : g 0 = 2)
  (h₄ : ∀ n, g (n + 1) = 2^(g n)) (node_linear_levels : (∀ y : ℕ, f 1 y = y + 2) ∧ (∀ y : ℕ, f 2 y = 2 * y + 3)) : ∀ y : ℕ, f 3 y + 3 = 2 ^ (y + 3) := by
  intro y
  induction y with
  | zero =>
    have h := h₁ 2
    norm_num [node_linear_levels.2] at h
    omega
  | succ y ih =>
    have h := h₂ 2 y
    norm_num [node_linear_levels.2] at h
    have he : y + 1 + 3 = (y + 3) + 1 := by omega
    rw [he, pow_succ]
    omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℕ → ℕ → ℕ)
  (g : ℕ → ℕ)
  (h₀ : ∀ y, f 0 y = y + 1)
  (h₁ : ∀ x, f (x + 1) 0 = f x 1)
  (h₂ : ∀ x y, f (x + 1) (y + 1) = f x (f (x + 1) y))
  (h₃ : g 0 = 2)
  (h₄ : ∀ n, g (n + 1) = 2^(g n)) (node_exponential_level : ∀ y : ℕ, f 3 y + 3 = 2 ^ (y + 3)) : f 4 1981 = g 1983 - 3 := by
  have align : ∀ y : ℕ, f 4 y + 3 = g (y + 2) := by
    intro y
    induction y with
    | zero =>
      have h := h₁ 3
      have e := node_exponential_level 1
      have g1 := h₄ 0
      have g2 := h₄ 1
      norm_num [h₃] at g1
      norm_num [g1] at g2
      norm_num at h e ⊢
      omega
    | succ y ih =>
      have h := h₂ 3 y
      have e := node_exponential_level (f 4 y)
      norm_num at h
      have he : y + 1 + 2 = (y + 2) + 1 := by omega
      rw [he, h₄, ← ih]
      omega
  have h := align 1981
  norm_num at h
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1981_p6 (f : ℕ → ℕ → ℕ)
  (g : ℕ → ℕ)
  (h₀ : ∀ y, f 0 y = y + 1)
  (h₁ : ∀ x, f (x + 1) 0 = f x 1)
  (h₂ : ∀ x y, f (x + 1) (y + 1) = f x (f (x + 1) y))
  (h₃ : g 0 = 2)
  (h₄ : ∀ n, g (n + 1) = 2^(g n)) : f 4 1981 = g 1983 - 3 := by
  have node_linear_levels : (∀ y : ℕ, f 1 y = y + 2) ∧ (∀ y : ℕ, f 2 y = 2 * y + 3) := by
    have first : ∀ y : ℕ, f 1 y = y + 2 := by
      intro y
      induction y with
      | zero => simpa [h₀] using h₁ 0
      | succ y ih =>
        have h := h₂ 0 y
        norm_num [h₀] at h
        omega
    refine ⟨first, ?_⟩
    intro y
    induction y with
    | zero => simpa [first] using h₁ 1
    | succ y ih =>
      have h := h₂ 1 y
      norm_num [first] at h
      omega
  have node_exponential_level : ∀ y : ℕ, f 3 y + 3 = 2 ^ (y + 3) := by
    intro y
    induction y with
    | zero =>
      have h := h₁ 2
      norm_num [node_linear_levels.2] at h
      omega
    | succ y ih =>
      have h := h₂ 2 y
      norm_num [node_linear_levels.2] at h
      have he : y + 1 + 3 = (y + 3) + 1 := by omega
      rw [he, pow_succ]
      omega
  have node_root : f 4 1981 = g 1983 - 3 := by
    have align : ∀ y : ℕ, f 4 y + 3 = g (y + 2) := by
      intro y
      induction y with
      | zero =>
        have h := h₁ 3
        have e := node_exponential_level 1
        have g1 := h₄ 0
        have g2 := h₄ 1
        norm_num [h₃] at g1
        norm_num [g1] at g2
        norm_num at h e ⊢
        omega
      | succ y ih =>
        have h := h₂ 3 y
        have e := node_exponential_level (f 4 y)
        norm_num at h
        have he : y + 1 + 2 = (y + 2) + 1 := by omega
        rw [he, h₄, ← ih]
        omega
    have h := align 1981
    norm_num at h
    omega
  exact node_root

#print axioms imo_1981_p6
