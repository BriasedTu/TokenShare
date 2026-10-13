import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : a ≠ 0 ∧ b ≠ 0) (h₁ : a ≠ b)
  (h₂ : ∀ x, f x = x ^ 2 + a * x + b) (h₃ : f a = 0) (h₄ : f b = 0) : a + b = -a := by
  have hp := h₂ a
  have hq := h₂ b
  have hz : (a - b) * (2 * a + b) = 0 := by nlinarith
  have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr h₁)
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : a ≠ 0 ∧ b ≠ 0) (h₁ : a ≠ b)
  (h₂ : ∀ x, f x = x ^ 2 + a * x + b) (h₃ : f a = 0) (h₄ : f b = 0) (node_root_sum : a + b = -a) : a * b = b := by
  have hq := h₂ b
  have hm := congrArg (fun t : ℝ => t * b) node_root_sum
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : a ≠ 0 ∧ b ≠ 0) (h₁ : a ≠ b)
  (h₂ : ∀ x, f x = x ^ 2 + a * x + b) (h₃ : f a = 0) (h₄ : f b = 0) (node_root_sum : a + b = -a) (node_root_product : a * b = b) : a = 1 ∧ b = -2 := by
  have ha : a = 1 := (mul_right_cancel₀ h₀.2) (by simpa using node_root_product)
  constructor
  · exact ha
  · linarith [node_root_sum]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_77 (a b : ℝ) (f : ℝ → ℝ) (h₀ : a ≠ 0 ∧ b ≠ 0) (h₁ : a ≠ b)
  (h₂ : ∀ x, f x = x ^ 2 + a * x + b) (h₃ : f a = 0) (h₄ : f b = 0) : a = 1 ∧ b = -2 := by
  have node_root_sum : a + b = -a := by
    have hp := h₂ a
    have hq := h₂ b
    have hz : (a - b) * (2 * a + b) = 0 := by nlinarith
    have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr h₁)
    linarith
  have node_root_product : a * b = b := by
    have hq := h₂ b
    have hm := congrArg (fun t : ℝ => t * b) node_root_sum
    nlinarith
  have node_root : a = 1 ∧ b = -2 := by
    have ha : a = 1 := (mul_right_cancel₀ h₀.2) (by simpa using node_root_product)
    constructor
    · exact ha
    · linarith [node_root_sum]
  exact node_root

#print axioms mathd_algebra_77
