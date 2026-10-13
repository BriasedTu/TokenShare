import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x b : ℝ) (h₀ : 0 < b) (h₁ : (7 : ℝ) ^ (x + 7) = 8 ^ x)
  (h₂ : x = Real.logb b (7 ^ 7)) : x * (Real.log 8 - Real.log 7) = 7 * Real.log 7 := by
  have hl := congrArg Real.log h₁
  rw [Real.log_rpow (by norm_num), Real.log_rpow (by norm_num)] at hl
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x b : ℝ) (h₀ : 0 < b) (h₁ : (7 : ℝ) ^ (x + 7) = 8 ^ x)
  (h₂ : x = Real.logb b (7 ^ 7)) (node_exponential_log_identity : x * (Real.log 8 - Real.log 7) = 7 * Real.log 7) : x * Real.log b = 7 * Real.log 7 := by
  have hp : 0 < Real.log 7 := Real.log_pos (by norm_num)
  have hx : x ≠ 0 := by intro he; rw [he] at node_exponential_log_identity; norm_num at node_exponential_log_identity
  have hb : b ≠ 1 := by
    intro he
    have hz : x = 0 := by simpa [he, Real.logb] using h₂
    exact hx hz
  have hlb : Real.log b ≠ 0 := Real.log_ne_zero_of_pos_of_ne_one h₀ hb
  have he : x = (7 * Real.log 7) / Real.log b := by simpa [Real.logb, Real.log_pow] using h₂
  exact (eq_div_iff hlb).mp he

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x b : ℝ) (h₀ : 0 < b) (h₁ : (7 : ℝ) ^ (x + 7) = 8 ^ x)
  (h₂ : x = Real.logb b (7 ^ 7)) (node_exponential_log_identity : x * (Real.log 8 - Real.log 7) = 7 * Real.log 7) (node_base_log_identity : x * Real.log b = 7 * Real.log 7) : b = 8 / 7 := by
  have hp : 0 < Real.log 7 := Real.log_pos (by norm_num)
  have hx : x ≠ 0 := by intro he; rw [he] at node_exponential_log_identity; norm_num at node_exponential_log_identity
  have hl : Real.log b = Real.log 8 - Real.log 7 :=
    mul_left_cancel₀ hx (node_base_log_identity.trans node_exponential_log_identity.symm)
  apply Real.log_injOn_pos h₀ (by norm_num : (0 : ℝ) < 8 / 7)
  rw [Real.log_div (by norm_num) (by norm_num)]
  exact hl

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2010_p11 (x b : ℝ) (h₀ : 0 < b) (h₁ : (7 : ℝ) ^ (x + 7) = 8 ^ x)
  (h₂ : x = Real.logb b (7 ^ 7)) : b = 8 / 7 := by
  have node_exponential_log_identity : x * (Real.log 8 - Real.log 7) = 7 * Real.log 7 := by
    have hl := congrArg Real.log h₁
    rw [Real.log_rpow (by norm_num), Real.log_rpow (by norm_num)] at hl
    nlinarith
  have node_base_log_identity : x * Real.log b = 7 * Real.log 7 := by
    have hp : 0 < Real.log 7 := Real.log_pos (by norm_num)
    have hx : x ≠ 0 := by intro he; rw [he] at node_exponential_log_identity; norm_num at node_exponential_log_identity
    have hb : b ≠ 1 := by
      intro he
      have hz : x = 0 := by simpa [he, Real.logb] using h₂
      exact hx hz
    have hlb : Real.log b ≠ 0 := Real.log_ne_zero_of_pos_of_ne_one h₀ hb
    have he : x = (7 * Real.log 7) / Real.log b := by simpa [Real.logb, Real.log_pow] using h₂
    exact (eq_div_iff hlb).mp he
  have node_root : b = 8 / 7 := by
    have hp : 0 < Real.log 7 := Real.log_pos (by norm_num)
    have hx : x ≠ 0 := by intro he; rw [he] at node_exponential_log_identity; norm_num at node_exponential_log_identity
    have hl : Real.log b = Real.log 8 - Real.log 7 :=
      mul_left_cancel₀ hx (node_base_log_identity.trans node_exponential_log_identity.symm)
    apply Real.log_injOn_pos h₀ (by norm_num : (0 : ℝ) < 8 / 7)
    rw [Real.log_div (by norm_num) (by norm_num)]
    exact hl
  exact node_root

#print axioms amc12a_2010_p11
