import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (f : ℝ → ℝ)
  (h₀ : 0 < a)
  (h₁ : ∀ x, f (x + a) = 1 / 2 + Real.sqrt (f x - (f x)^2)) : ∀ x : ℝ, 1 / 2 ≤ f x ∧ f x ≤ 1 := by
  intro x
  have hx := h₁ (x - a)
  have he : x - a + a = x := by ring
  rw [he] at hx
  have hs := Real.sqrt_nonneg (f (x - a) - f (x - a) ^ 2)
  have hu : Real.sqrt (f (x - a) - f (x - a) ^ 2) ≤ 1 / 2 := by
    apply Real.sqrt_le_iff.mpr
    constructor
    · norm_num
    · nlinarith [sq_nonneg (f (x - a) - 1 / 2)]
  constructor <;> linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (f : ℝ → ℝ)
  (h₀ : 0 < a)
  (h₁ : ∀ x, f (x + a) = 1 / 2 + Real.sqrt (f x - (f x)^2)) (node_global_range : ∀ x : ℝ, 1 / 2 ≤ f x ∧ f x ≤ 1) : ∀ x : ℝ, f (x + a) - f (x + a) ^ 2 = (f x - 1 / 2) ^ 2 := by
  intro x
  have hr := node_global_range x
  have hnon : 0 ≤ f x - f x ^ 2 := by nlinarith
  have hs := Real.sq_sqrt hnon
  rw [h₁ x]
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℝ)
  (f : ℝ → ℝ)
  (h₀ : 0 < a)
  (h₁ : ∀ x, f (x + a) = 1 / 2 + Real.sqrt (f x - (f x)^2)) (node_global_range : ∀ x : ℝ, 1 / 2 ≤ f x ∧ f x ≤ 1) (node_composed_radicand : ∀ x : ℝ, f (x + a) - f (x + a) ^ 2 = (f x - 1 / 2) ^ 2) : ∃ b > 0, ∀ x, f (x + b) = f x := by
  refine ⟨2 * a, by linarith, ?_⟩
  intro x
  have he : x + 2 * a = (x + a) + a := by ring
  rw [he, h₁ (x + a), node_composed_radicand x]
  rw [Real.sqrt_sq (by linarith [(node_global_range x).1] : 0 ≤ f x - 1 / 2)]
  ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1968_p5_1 (a : ℝ)
  (f : ℝ → ℝ)
  (h₀ : 0 < a)
  (h₁ : ∀ x, f (x + a) = 1 / 2 + Real.sqrt (f x - (f x)^2)) : ∃ b > 0, ∀ x, f (x + b) = f x := by
  have node_global_range : ∀ x : ℝ, 1 / 2 ≤ f x ∧ f x ≤ 1 := by
    intro x
    have hx := h₁ (x - a)
    have he : x - a + a = x := by ring
    rw [he] at hx
    have hs := Real.sqrt_nonneg (f (x - a) - f (x - a) ^ 2)
    have hu : Real.sqrt (f (x - a) - f (x - a) ^ 2) ≤ 1 / 2 := by
      apply Real.sqrt_le_iff.mpr
      constructor
      · norm_num
      · nlinarith [sq_nonneg (f (x - a) - 1 / 2)]
    constructor <;> linarith
  have node_composed_radicand : ∀ x : ℝ, f (x + a) - f (x + a) ^ 2 = (f x - 1 / 2) ^ 2 := by
    intro x
    have hr := node_global_range x
    have hnon : 0 ≤ f x - f x ^ 2 := by nlinarith
    have hs := Real.sq_sqrt hnon
    rw [h₁ x]
    nlinarith
  have node_root : ∃ b > 0, ∀ x, f (x + b) = f x := by
    refine ⟨2 * a, by linarith, ?_⟩
    intro x
    have he : x + 2 * a = (x + a) + a := by ring
    rw [he, h₁ (x + a), node_composed_radicand x]
    rw [Real.sqrt_sq (by linarith [(node_global_range x).1] : 0 ≤ f x - 1 / 2)]
    ring
  exact node_root

#print axioms imo_1968_p5_1
