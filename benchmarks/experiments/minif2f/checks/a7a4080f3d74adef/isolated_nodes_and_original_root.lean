import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
  (a b c : ℕ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 ≤ x)
  (h₁ : 2 * x^2 = 4 * x + 9)
  (h₂ : x = (a + Real.sqrt b) / c)
  (h₃ : c = 2) : a ≤ 5 ∧ b ≤ 35 := by
  have hx : x < 7/2 := by nlinarith [h₀.2.2.2]
  have hs := Real.sqrt_nonneg (b:ℝ)
  have hb : (1:ℝ) ≤ b := by exact_mod_cast h₀.2.1
  have ha : (1:ℝ) ≤ a := by exact_mod_cast h₀.1
  have hr : 1 ≤ Real.sqrt (b:ℝ) := (Real.le_sqrt (by norm_num) (by positivity)).2 (by simpa using hb)
  have he : 2*x=(a:ℝ)+Real.sqrt b := by rw [h₃] at h₂; norm_num at h₂; linarith
  have har : (a:ℝ) < 6 := by linarith
  have hbr : (b:ℝ) < 36 := by nlinarith [Real.sq_sqrt (by positivity : 0 ≤ (b:ℝ))]
  have ha' : a < 6 := by exact_mod_cast har
  have hb' : b < 36 := by exact_mod_cast hbr
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
  (a b c : ℕ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 ≤ x)
  (h₁ : 2 * x^2 = 4 * x + 9)
  (h₂ : x = (a + Real.sqrt b) / c)
  (h₃ : c = 2) : ((a:ℤ)^2+b-4*a-18)^2 = (2*(a:ℤ)-4)^2*b := by
  have he : 2*x=(a:ℝ)+Real.sqrt b := by rw [h₃] at h₂; norm_num at h₂; linarith
  have hs := Real.sq_sqrt (by positivity : 0 ≤ (b:ℝ))
  have hp : (a:ℝ)^2+b-4*a-18=(4-2*(a:ℝ))*Real.sqrt b := by nlinarith
  have hq : ((a:ℝ)^2+b-4*a-18)^2=(2*(a:ℝ)-4)^2*b := by rw [hp]; nlinarith only [hs]
  exact_mod_cast hq

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : ℝ)
  (a b c : ℕ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 ≤ x)
  (h₁ : 2 * x^2 = 4 * x + 9)
  (h₂ : x = (a + Real.sqrt b) / c)
  (h₃ : c = 2) (node_coordinate_bounds : a ≤ 5 ∧ b ≤ 35) (node_integer_eliminant : ((a:ℤ)^2+b-4*a-18)^2 = (2*(a:ℤ)-4)^2*b) : a + b + c = 26 := by
  rcases node_coordinate_bounds with ⟨ha, hb⟩
  interval_cases a <;> interval_cases b <;> norm_num at *
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_320 (x : ℝ)
  (a b c : ℕ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 ≤ x)
  (h₁ : 2 * x^2 = 4 * x + 9)
  (h₂ : x = (a + Real.sqrt b) / c)
  (h₃ : c = 2) : a + b + c = 26 := by
  have node_coordinate_bounds : a ≤ 5 ∧ b ≤ 35 := by
    have hx : x < 7/2 := by nlinarith [h₀.2.2.2]
    have hs := Real.sqrt_nonneg (b:ℝ)
    have hb : (1:ℝ) ≤ b := by exact_mod_cast h₀.2.1
    have ha : (1:ℝ) ≤ a := by exact_mod_cast h₀.1
    have hr : 1 ≤ Real.sqrt (b:ℝ) := (Real.le_sqrt (by norm_num) (by positivity)).2 (by simpa using hb)
    have he : 2*x=(a:ℝ)+Real.sqrt b := by rw [h₃] at h₂; norm_num at h₂; linarith
    have har : (a:ℝ) < 6 := by linarith
    have hbr : (b:ℝ) < 36 := by nlinarith [Real.sq_sqrt (by positivity : 0 ≤ (b:ℝ))]
    have ha' : a < 6 := by exact_mod_cast har
    have hb' : b < 36 := by exact_mod_cast hbr
    omega
  have node_integer_eliminant : ((a:ℤ)^2+b-4*a-18)^2 = (2*(a:ℤ)-4)^2*b := by
    have he : 2*x=(a:ℝ)+Real.sqrt b := by rw [h₃] at h₂; norm_num at h₂; linarith
    have hs := Real.sq_sqrt (by positivity : 0 ≤ (b:ℝ))
    have hp : (a:ℝ)^2+b-4*a-18=(4-2*(a:ℝ))*Real.sqrt b := by nlinarith
    have hq : ((a:ℝ)^2+b-4*a-18)^2=(2*(a:ℝ)-4)^2*b := by rw [hp]; nlinarith only [hs]
    exact_mod_cast hq
  have node_root : a + b + c = 26 := by
    rcases node_coordinate_bounds with ⟨ha, hb⟩
    interval_cases a <;> interval_cases b <;> norm_num at *
    omega
  exact node_root

#print axioms mathd_algebra_320
