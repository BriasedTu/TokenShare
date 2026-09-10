import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = (x^2 + (18 * x +  30) - 2 * Real.sqrt (x^2 + (18 * x + 45))))
  (h₁ : Fintype (f⁻¹' {0})) : ∀ x : ℝ, f x = 0 ↔ x^2 + 18*x + 20 = 0 := by
  intro x
  constructor
  · intro hx
    have he := h₀ x
    have hs := Real.sqrt_nonneg (x^2 + (18*x+45))
    have ht : 0 ≤ x^2 + (18*x+45) := by nlinarith
    have hsq := Real.sq_sqrt ht
    have hz : (Real.sqrt (x^2+(18*x+45))-5)*(Real.sqrt (x^2+(18*x+45))+3)=0 := by nlinarith
    have hv := (mul_eq_zero.mp hz).resolve_right (by linarith : Real.sqrt (x^2+(18*x+45))+3 ≠ 0)
    nlinarith
  · intro hx
    rw [h₀ x]
    have ht : x^2+(18*x+45)=25 := by nlinarith
    rw [ht]
    norm_num
    nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = (x^2 + (18 * x +  30) - 2 * Real.sqrt (x^2 + (18 * x + 45))))
  (h₁ : Fintype (f⁻¹' {0})) (node_zero_equivalence : ∀ x : ℝ, f x = 0 ↔ x^2 + 18*x + 20 = 0) : f⁻¹' {0} = {-9-Real.sqrt 61, -9+Real.sqrt 61} := by
  ext x
  simp only [Set.mem_preimage, Set.mem_singleton_iff, Set.mem_insert_iff]
  rw [node_zero_equivalence x]
  have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 61)
  constructor
  · intro hx
    have hz : (x+9-Real.sqrt 61)*(x+9+Real.sqrt 61)=0 := by nlinarith
    rcases mul_eq_zero.mp hz with hp | hm
    · exact Or.inr (by linarith)
    · exact Or.inl (by linarith)
  · rintro (rfl | rfl) <;> nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = (x^2 + (18 * x +  30) - 2 * Real.sqrt (x^2 + (18 * x + 45))))
  (h₁ : Fintype (f⁻¹' {0})) (node_zero_set : f⁻¹' {0} = {-9-Real.sqrt 61, -9+Real.sqrt 61}) : ∏ x ∈ (f⁻¹' {0}).toFinset, x = 20 := by
  classical
  have hf : (f⁻¹' {0}).toFinset = {-9-Real.sqrt 61, -9+Real.sqrt 61} := by
    ext x
    simp [node_zero_set]
  have hp : 0 < Real.sqrt (61:ℝ) := Real.sqrt_pos.2 (by norm_num)
  have hn : -9-Real.sqrt (61:ℝ) ≠ -9+Real.sqrt 61 := by linarith
  rw [hf]
  simp [hn]
  nlinarith [Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 61)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1983_p3 (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = (x^2 + (18 * x +  30) - 2 * Real.sqrt (x^2 + (18 * x + 45))))
  (h₁ : Fintype (f⁻¹' {0})) : ∏ x ∈ (f⁻¹' {0}).toFinset, x = 20 := by
  have node_zero_equivalence : ∀ x : ℝ, f x = 0 ↔ x^2 + 18*x + 20 = 0 := by
    intro x
    constructor
    · intro hx
      have he := h₀ x
      have hs := Real.sqrt_nonneg (x^2 + (18*x+45))
      have ht : 0 ≤ x^2 + (18*x+45) := by nlinarith
      have hsq := Real.sq_sqrt ht
      have hz : (Real.sqrt (x^2+(18*x+45))-5)*(Real.sqrt (x^2+(18*x+45))+3)=0 := by nlinarith
      have hv := (mul_eq_zero.mp hz).resolve_right (by linarith : Real.sqrt (x^2+(18*x+45))+3 ≠ 0)
      nlinarith
    · intro hx
      rw [h₀ x]
      have ht : x^2+(18*x+45)=25 := by nlinarith
      rw [ht]
      norm_num
      nlinarith
  have node_zero_set : f⁻¹' {0} = {-9-Real.sqrt 61, -9+Real.sqrt 61} := by
    ext x
    simp only [Set.mem_preimage, Set.mem_singleton_iff, Set.mem_insert_iff]
    rw [node_zero_equivalence x]
    have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 61)
    constructor
    · intro hx
      have hz : (x+9-Real.sqrt 61)*(x+9+Real.sqrt 61)=0 := by nlinarith
      rcases mul_eq_zero.mp hz with hp | hm
      · exact Or.inr (by linarith)
      · exact Or.inl (by linarith)
    · rintro (rfl | rfl) <;> nlinarith
  have node_root : ∏ x ∈ (f⁻¹' {0}).toFinset, x = 20 := by
    classical
    have hf : (f⁻¹' {0}).toFinset = {-9-Real.sqrt 61, -9+Real.sqrt 61} := by
      ext x
      simp [node_zero_set]
    have hp : 0 < Real.sqrt (61:ℝ) := Real.sqrt_pos.2 (by norm_num)
    have hn : -9-Real.sqrt (61:ℝ) ≠ -9+Real.sqrt 61 := by linarith
    rw [hf]
    simp [hn]
    nlinarith [Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 61)]
  exact node_root

#print axioms aime_1983_p3
