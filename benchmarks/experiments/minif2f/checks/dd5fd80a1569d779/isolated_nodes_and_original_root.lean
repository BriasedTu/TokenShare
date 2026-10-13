import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : ∀ x, f x = x ^ 2 + a * x + b) (h₁ : 2 * a ≠ b)
  (h₂ : f (2 * a) = 0) (h₃ : f b = 0) : 2 * a + b = -a := by
  have hp := h₀ (2 * a)
  have hq := h₀ b
  have hz : (2 * a - b) * (3 * a + b) = 0 := by nlinarith
  have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr h₁)
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : ∀ x, f x = x ^ 2 + a * x + b) (h₁ : 2 * a ≠ b)
  (h₂ : f (2 * a) = 0) (h₃ : f b = 0) (node_root_sum : 2 * a + b = -a) : a = 1 / 2 := by
  have hp := h₀ (2 * a)
  have ha : a ≠ 0 := by
    intro ha
    have hb : b = 0 := by linarith [node_root_sum]
    exact h₁ (by rw [ha, hb]; ring)
  have hz : a * (2 * a - 1) = 0 := by nlinarith [node_root_sum]
  have he := (mul_eq_zero.mp hz).resolve_left ha
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (f : ℝ → ℝ) (h₀ : ∀ x, f x = x ^ 2 + a * x + b) (h₁ : 2 * a ≠ b)
  (h₂ : f (2 * a) = 0) (h₃ : f b = 0) (node_root_sum : 2 * a + b = -a) (node_first_coefficient : a = 1 / 2) : a + b = -1 := by
  linarith [node_root_sum, node_first_coefficient]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_206 (a b : ℝ) (f : ℝ → ℝ) (h₀ : ∀ x, f x = x ^ 2 + a * x + b) (h₁ : 2 * a ≠ b)
  (h₂ : f (2 * a) = 0) (h₃ : f b = 0) : a + b = -1 := by
  have node_root_sum : 2 * a + b = -a := by
    have hp := h₀ (2 * a)
    have hq := h₀ b
    have hz : (2 * a - b) * (3 * a + b) = 0 := by nlinarith
    have hs := (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr h₁)
    linarith
  have node_first_coefficient : a = 1 / 2 := by
    have hp := h₀ (2 * a)
    have ha : a ≠ 0 := by
      intro ha
      have hb : b = 0 := by linarith [node_root_sum]
      exact h₁ (by rw [ha, hb]; ring)
    have hz : a * (2 * a - 1) = 0 := by nlinarith [node_root_sum]
    have he := (mul_eq_zero.mp hz).resolve_left ha
    linarith
  have node_root : a + b = -1 := by
    linarith [node_root_sum, node_first_coefficient]
  exact node_root

#print axioms mathd_algebra_206
