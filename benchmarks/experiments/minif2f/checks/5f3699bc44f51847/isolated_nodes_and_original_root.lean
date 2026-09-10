import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : 3 ≤ a * b + b * c + c * a) : (a+b+c)^2 ≤ (a/Real.sqrt (a+b)+b/Real.sqrt (b+c)+c/Real.sqrt (c+a))*(a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a)) := by
  rcases h₀ with ⟨ha,hb,hc⟩
  have hu : Real.sqrt (a+b) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
  have hv : Real.sqrt (b+c) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
  have hw : Real.sqrt (c+a) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
  have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.univ : Finset (Fin 3)) (r := ![a,b,c]) (f := ![a/Real.sqrt (a+b),b/Real.sqrt (b+c),c/Real.sqrt (c+a)]) (g := ![a*Real.sqrt (a+b),b*Real.sqrt (b+c),c*Real.sqrt (c+a)]) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> field_simp)
  simpa [Fin.sum_univ_succ, add_assoc] using h

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : 3 ≤ a * b + b * c + c * a) : (a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a))^2 ≤ (a+b+c)*((a+b+c)^2-(a*b+b*c+c*a)) := by
  rcases h₀ with ⟨ha,hb,hc⟩
  have hu := Real.sq_sqrt (show 0 ≤ a+b by linarith)
  have hv := Real.sq_sqrt (show 0 ≤ b+c by linarith)
  have hw := Real.sq_sqrt (show 0 ≤ c+a by linarith)
  have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.univ : Finset (Fin 3)) (r := ![a*Real.sqrt (a+b),b*Real.sqrt (b+c),c*Real.sqrt (c+a)]) (f := ![a,b,c]) (g := ![a*(a+b),b*(b+c),c*(c+a)]) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> nlinarith)
  simp [Fin.sum_univ_succ] at h
  nlinarith only [h]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : 3 ≤ a * b + b * c + c * a) : 9/2*((a+b+c)^2-(a*b+b*c+c*a)) ≤ (a+b+c)^3 := by
  have hs : 3 ≤ a+b+c := by nlinarith [sq_nonneg (a-b),sq_nonneg (b-c),sq_nonneg (c-a),h₀.1,h₀.2.1,h₀.2.2]
  have hp := mul_nonneg (sq_nonneg (a+b+c-3)) (show 0 ≤ a+b+c+3/2 by linarith)
  nlinarith only [hp,h₁]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : 3 ≤ a * b + b * c + c * a) (node_first_cauchy_bound : (a+b+c)^2 ≤ (a/Real.sqrt (a+b)+b/Real.sqrt (b+c)+c/Real.sqrt (c+a))*(a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a))) (node_second_cauchy_bound : (a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a))^2 ≤ (a+b+c)*((a+b+c)^2-(a*b+b*c+c*a))) (node_symmetric_polynomial_bound : 9/2*((a+b+c)^2-(a*b+b*c+c*a)) ≤ (a+b+c)^3) : 3 / Real.sqrt 2 ≤ a / Real.sqrt (a + b) + b / Real.sqrt (b + c) + c / Real.sqrt (c + a) := by
  rcases h₀ with ⟨ha,hb,hc⟩
  let T := a/Real.sqrt (a+b)+b/Real.sqrt (b+c)+c/Real.sqrt (c+a)
  let A := a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a)
  let U := a+b+c
  let D := U^2-(a*b+b*c+c*a)
  have ht : 0 < T := by dsimp [T]; positivity
  have hA : 0 < A := by dsimp [A]; positivity
  have hU : 0 < U := by dsimp [U]; linarith
  have hD : 0 < D := by dsimp [D,U]; nlinarith [mul_pos ha hb,mul_pos hb hc,mul_pos hc ha]
  have h1 : U^2 ≤ T*A := node_first_cauchy_bound
  have h2 : A^2 ≤ U*D := node_second_cauchy_bound
  have h3 : 9/2*D ≤ U^3 := node_symmetric_polynomial_bound
  have h4 : U^4 ≤ (T*A)^2 := by nlinarith only [h1,sq_nonneg (U^2-T*A)]
  have h5 := mul_le_mul_of_nonneg_left h2 (sq_nonneg T)
  have h6 : U^3 ≤ T^2*D := by nlinarith only [h4,h5,hU]
  have h7 : 9 ≤ 2*T^2 := by nlinarith only [h3,h6,hD]
  have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 2)
  have hp := Real.sqrt_pos.2 (by norm_num : (0:ℝ) < 2)
  change 3/Real.sqrt 2 ≤ T
  apply (div_le_iff₀ hp).2
  nlinarith only [h7,hs,ht,hp,sq_nonneg (T*Real.sqrt 2-3)]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_abpbcpcageq3_sumaonsqrtapbgeq3onsqrt2 (a b c : ℝ)
  (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : 3 ≤ a * b + b * c + c * a) : 3 / Real.sqrt 2 ≤ a / Real.sqrt (a + b) + b / Real.sqrt (b + c) + c / Real.sqrt (c + a) := by
  have node_first_cauchy_bound : (a+b+c)^2 ≤ (a/Real.sqrt (a+b)+b/Real.sqrt (b+c)+c/Real.sqrt (c+a))*(a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a)) := by
    rcases h₀ with ⟨ha,hb,hc⟩
    have hu : Real.sqrt (a+b) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
    have hv : Real.sqrt (b+c) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
    have hw : Real.sqrt (c+a) ≠ 0 := (Real.sqrt_pos.2 (by linarith)).ne'
    have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.univ : Finset (Fin 3)) (r := ![a,b,c]) (f := ![a/Real.sqrt (a+b),b/Real.sqrt (b+c),c/Real.sqrt (c+a)]) (g := ![a*Real.sqrt (a+b),b*Real.sqrt (b+c),c*Real.sqrt (c+a)]) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> field_simp)
    simpa [Fin.sum_univ_succ, add_assoc] using h
  have node_second_cauchy_bound : (a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a))^2 ≤ (a+b+c)*((a+b+c)^2-(a*b+b*c+c*a)) := by
    rcases h₀ with ⟨ha,hb,hc⟩
    have hu := Real.sq_sqrt (show 0 ≤ a+b by linarith)
    have hv := Real.sq_sqrt (show 0 ≤ b+c by linarith)
    have hw := Real.sq_sqrt (show 0 ≤ c+a by linarith)
    have h := Finset.sum_sq_le_sum_mul_sum_of_sq_eq_mul (Finset.univ : Finset (Fin 3)) (r := ![a*Real.sqrt (a+b),b*Real.sqrt (b+c),c*Real.sqrt (c+a)]) (f := ![a,b,c]) (g := ![a*(a+b),b*(b+c),c*(c+a)]) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> positivity) (by intro i hi; fin_cases i <;> simp <;> nlinarith)
    simp [Fin.sum_univ_succ] at h
    nlinarith only [h]
  have node_symmetric_polynomial_bound : 9/2*((a+b+c)^2-(a*b+b*c+c*a)) ≤ (a+b+c)^3 := by
    have hs : 3 ≤ a+b+c := by nlinarith [sq_nonneg (a-b),sq_nonneg (b-c),sq_nonneg (c-a),h₀.1,h₀.2.1,h₀.2.2]
    have hp := mul_nonneg (sq_nonneg (a+b+c-3)) (show 0 ≤ a+b+c+3/2 by linarith)
    nlinarith only [hp,h₁]
  have node_root : 3 / Real.sqrt 2 ≤ a / Real.sqrt (a + b) + b / Real.sqrt (b + c) + c / Real.sqrt (c + a) := by
    rcases h₀ with ⟨ha,hb,hc⟩
    let T := a/Real.sqrt (a+b)+b/Real.sqrt (b+c)+c/Real.sqrt (c+a)
    let A := a*Real.sqrt (a+b)+b*Real.sqrt (b+c)+c*Real.sqrt (c+a)
    let U := a+b+c
    let D := U^2-(a*b+b*c+c*a)
    have ht : 0 < T := by dsimp [T]; positivity
    have hA : 0 < A := by dsimp [A]; positivity
    have hU : 0 < U := by dsimp [U]; linarith
    have hD : 0 < D := by dsimp [D,U]; nlinarith [mul_pos ha hb,mul_pos hb hc,mul_pos hc ha]
    have h1 : U^2 ≤ T*A := node_first_cauchy_bound
    have h2 : A^2 ≤ U*D := node_second_cauchy_bound
    have h3 : 9/2*D ≤ U^3 := node_symmetric_polynomial_bound
    have h4 : U^4 ≤ (T*A)^2 := by nlinarith only [h1,sq_nonneg (U^2-T*A)]
    have h5 := mul_le_mul_of_nonneg_left h2 (sq_nonneg T)
    have h6 : U^3 ≤ T^2*D := by nlinarith only [h4,h5,hU]
    have h7 : 9 ≤ 2*T^2 := by nlinarith only [h3,h6,hD]
    have hs := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 2)
    have hp := Real.sqrt_pos.2 (by norm_num : (0:ℝ) < 2)
    change 3/Real.sqrt 2 ≤ T
    apply (div_le_iff₀ hp).2
    nlinarith only [h7,hs,ht,hp,sq_nonneg (T*Real.sqrt 2-3)]
  exact node_root

#print axioms algebra_abpbcpcageq3_sumaonsqrtapbgeq3onsqrt2
