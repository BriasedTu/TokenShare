import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a m c : ℕ)
  (h₀ : a + m + c = 12) : 4*((a:ℝ)+1)*((m:ℝ)+1) ≤ ((a:ℝ)+m+2)^2 := by
  nlinarith [sq_nonneg ((a:ℝ)-m)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a m c : ℕ)
  (h₀ : a + m + c = 12) : ((c:ℝ)+1)*(14-(c:ℝ))^2 ≤ 500 := by
  have hc : c ≤ 12 := by omega
  have hcr : (c:ℝ) ≤ 12 := by exact_mod_cast hc
  have hp := mul_nonneg (sq_nonneg ((c:ℝ)-4)) (by linarith : 0 ≤ 19-(c:ℝ))
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a m c : ℕ)
  (h₀ : a + m + c = 12) (node_pair_bound : 4*((a:ℝ)+1)*((m:ℝ)+1) ≤ ((a:ℝ)+m+2)^2) (node_cubic_bound : ((c:ℝ)+1)*(14-(c:ℝ))^2 ≤ 500) : a*m*c + a*m + m*c + a*c ≤ 112 := by
  have hs : (a:ℝ)+m+c=12 := by exact_mod_cast h₀
  have he : (a:ℝ)+m+2=14-(c:ℝ) := by linarith
  have hp := mul_le_mul_of_nonneg_right node_pair_bound (by positivity : 0 ≤ (c:ℝ)+1)
  rw [he] at hp
  have hf : (a:ℝ)*m*c+a*m+m*c+a*c ≤ 112 := by nlinarith
  exact_mod_cast hf

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12_2000_p12 (a m c : ℕ)
  (h₀ : a + m + c = 12) : a*m*c + a*m + m*c + a*c ≤ 112 := by
  have node_pair_bound : 4*((a:ℝ)+1)*((m:ℝ)+1) ≤ ((a:ℝ)+m+2)^2 := by
    nlinarith [sq_nonneg ((a:ℝ)-m)]
  have node_cubic_bound : ((c:ℝ)+1)*(14-(c:ℝ))^2 ≤ 500 := by
    have hc : c ≤ 12 := by omega
    have hcr : (c:ℝ) ≤ 12 := by exact_mod_cast hc
    have hp := mul_nonneg (sq_nonneg ((c:ℝ)-4)) (by linarith : 0 ≤ 19-(c:ℝ))
    nlinarith
  have node_root : a*m*c + a*m + m*c + a*c ≤ 112 := by
    have hs : (a:ℝ)+m+c=12 := by exact_mod_cast h₀
    have he : (a:ℝ)+m+2=14-(c:ℝ) := by linarith
    have hp := mul_le_mul_of_nonneg_right node_pair_bound (by positivity : 0 ≤ (c:ℝ)+1)
    rw [he] at hp
    have hf : (a:ℝ)*m*c+a*m+m*c+a*c ≤ 112 := by nlinarith
    exact_mod_cast hf
  exact node_root

#print axioms amc12_2000_p12
