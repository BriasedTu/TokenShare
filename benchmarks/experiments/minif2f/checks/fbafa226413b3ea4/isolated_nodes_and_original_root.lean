import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ) : (∀ a b : ℤ, f (2 * a) + 2 * f b = f (f (a + b))) → (∀ a b : ℤ, f (a + b) = f a + f b - f 0) := by
  intro h a b
  have hab := h a b
  have hs := h 0 (a + b)
  have ha := h a 0
  have ha' := h 0 a
  norm_num at hs ha ha'
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ) : (∀ a b : ℤ, f (a + b) = f a + f b - f 0) → ∀ z : ℤ, f z = (f 1 - f 0) * z + f 0 := by
  intro h z
  induction z using Int.induction_on with
  | zero => ring
  | succ i ih =>
    have hs := h (i : ℤ) 1
    rw [ih] at hs
    nlinarith
  | pred i ih =>
    have hs := h (-(i : ℤ) - 1) 1
    have he : -(i : ℤ) - 1 + 1 = -(i : ℤ) := by ring
    rw [he, ih] at hs
    nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (f : ℤ → ℤ) (node_additive_shift : (∀ a b : ℤ, f (2 * a) + 2 * f b = f (f (a + b))) → (∀ a b : ℤ, f (a + b) = f a + f b - f 0)) (node_integer_affine_form : (∀ a b : ℤ, f (a + b) = f a + f b - f 0) → ∀ z : ℤ, f z = (f 1 - f 0) * z + f 0) : ((∀ a b, f (2 * a) + (2 * f b) = f (f (a + b))) ↔ (∀ z, f z = 0 \/ ∃ c, ∀ z, f z = 2 * z + c)) := by
  constructor
  · intro h z
    have hf := node_integer_affine_form (node_additive_shift h)
    have e0 := h 0 0
    have e1 := h 0 1
    norm_num at e0 e1
    rw [hf (f 0)] at e0
    rw [hf (f 1)] at e1
    have hs : f 1 - f 0 = 0 ∨ f 1 - f 0 = 2 := by
      have he : (f 1 - f 0) * (f 1 - f 0 - 2) = 0 := by nlinarith
      rcases mul_eq_zero.mp he with he | he
      · exact Or.inl he
      · right; omega
    rcases hs with hs | hs
    · left
      have hz : f 0 = 0 := by nlinarith [e0]
      rw [hf z, hs, hz]
      ring
    · right
      refine ⟨f 0, ?_⟩
      intro w
      rw [hf w, hs]
  · intro h
    by_cases hz : ∀ z : ℤ, f z = 0
    · intro a b
      simp only [hz]
      ring
    · obtain ⟨z, hz⟩ := not_forall.mp hz
      rcases h z with he | ⟨c, hc⟩
      · exact False.elim (hz he)
      · intro a b
        simp only [hc]
        ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_2019_p1 (f : ℤ → ℤ) : ((∀ a b, f (2 * a) + (2 * f b) = f (f (a + b))) ↔ (∀ z, f z = 0 \/ ∃ c, ∀ z, f z = 2 * z + c)) := by
  have node_additive_shift : (∀ a b : ℤ, f (2 * a) + 2 * f b = f (f (a + b))) → (∀ a b : ℤ, f (a + b) = f a + f b - f 0) := by
    intro h a b
    have hab := h a b
    have hs := h 0 (a + b)
    have ha := h a 0
    have ha' := h 0 a
    norm_num at hs ha ha'
    omega
  have node_integer_affine_form : (∀ a b : ℤ, f (a + b) = f a + f b - f 0) → ∀ z : ℤ, f z = (f 1 - f 0) * z + f 0 := by
    intro h z
    induction z using Int.induction_on with
    | zero => ring
    | succ i ih =>
      have hs := h (i : ℤ) 1
      rw [ih] at hs
      nlinarith
    | pred i ih =>
      have hs := h (-(i : ℤ) - 1) 1
      have he : -(i : ℤ) - 1 + 1 = -(i : ℤ) := by ring
      rw [he, ih] at hs
      nlinarith
  have node_root : ((∀ a b, f (2 * a) + (2 * f b) = f (f (a + b))) ↔ (∀ z, f z = 0 \/ ∃ c, ∀ z, f z = 2 * z + c)) := by
    constructor
    · intro h z
      have hf := node_integer_affine_form (node_additive_shift h)
      have e0 := h 0 0
      have e1 := h 0 1
      norm_num at e0 e1
      rw [hf (f 0)] at e0
      rw [hf (f 1)] at e1
      have hs : f 1 - f 0 = 0 ∨ f 1 - f 0 = 2 := by
        have he : (f 1 - f 0) * (f 1 - f 0 - 2) = 0 := by nlinarith
        rcases mul_eq_zero.mp he with he | he
        · exact Or.inl he
        · right; omega
      rcases hs with hs | hs
      · left
        have hz : f 0 = 0 := by nlinarith [e0]
        rw [hf z, hs, hz]
        ring
      · right
        refine ⟨f 0, ?_⟩
        intro w
        rw [hf w, hs]
    · intro h
      by_cases hz : ∀ z : ℤ, f z = 0
      · intro a b
        simp only [hz]
        ring
      · obtain ⟨z, hz⟩ := not_forall.mp hz
        rcases h z with he | ⟨c, hc⟩
        · exact False.elim (hz he)
        · intro a b
          simp only [hc]
          ring
  exact node_root

#print axioms imo_2019_p1
