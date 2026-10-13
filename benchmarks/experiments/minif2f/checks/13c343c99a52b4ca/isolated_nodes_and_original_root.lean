import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k m n : ℕ)
  (t : ℝ)
  (h₀ : 0 < k ∧ 0 < m ∧ 0 < n)
  (h₁ : Nat.gcd m n = 1)
  (h₂ : (1 + Real.sin t) * (1 + Real.cos t) = 5/4)
  (h₃ : (1 - Real.sin t) * (1- Real.cos t) = m/n - Real.sqrt k) : (1-Real.sin t)*(1-Real.cos t)=13/4-Real.sqrt 10 := by
  have hs := Real.sin_sq_add_cos_sq t
  have hp : Real.sin t*Real.cos t ≤ 1/2 := by nlinarith [sq_nonneg (Real.sin t-Real.cos t)]
  have he : (2*(Real.sin t+Real.cos t+1))^2=10 := by nlinarith [h₂]
  have hn : 0 ≤ 2*(Real.sin t+Real.cos t+1) := by nlinarith [h₂]
  have hr : 2*(Real.sin t+Real.cos t+1)=Real.sqrt 10 := by nlinarith [Real.sqrt_nonneg 10, Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 10)]
  nlinarith [h₂]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k m n : ℕ)
  (t : ℝ)
  (h₀ : 0 < k ∧ 0 < m ∧ 0 < n)
  (h₁ : Nat.gcd m n = 1)
  (h₂ : (1 + Real.sin t) * (1 + Real.cos t) = 5/4)
  (h₃ : (1 - Real.sin t) * (1- Real.cos t) = m/n - Real.sqrt k) (node_trigonometric_radical_value : (1-Real.sin t)*(1-Real.cos t)=13/4-Real.sqrt 10) : (m:ℝ)/n=13/4 ∧ k=10 := by
  have hn : (n:ℝ) ≠ 0 := by exact_mod_cast h₀.2.2.ne'
  let q : ℚ := (m:ℚ)/n-13/4
  have hq : (q:ℝ)=(m:ℝ)/n-13/4 := by dsimp [q]; push_cast; rfl
  have hr : Real.sqrt k=Real.sqrt 10+(q:ℝ) := by linarith [h₃]
  have hs := Real.sq_sqrt (by positivity : 0 ≤ (k:ℝ))
  have hs10 := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 10)
  rw [hr] at hs
  have hi : Irrational (Real.sqrt 10) := by norm_num
  have hq0 : q=0 := by
    by_contra hqne
    have hqr : (q:ℝ) ≠ 0 := by exact_mod_cast hqne
    apply hi.ne_rat (((k:ℚ)-10-q^2)/(2*q))
    push_cast
    apply (eq_div_iff (mul_ne_zero (by norm_num) hqr)).2
    nlinarith only [hs,hs10]
  have hqr : (q:ℝ)=0 := by exact_mod_cast hq0
  constructor
  · linarith
  · have hk : (k:ℝ)=10 := by rw [hqr] at hs; nlinarith only [hs,hs10]
    exact_mod_cast hk

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (k m n : ℕ)
  (t : ℝ)
  (h₀ : 0 < k ∧ 0 < m ∧ 0 < n)
  (h₁ : Nat.gcd m n = 1)
  (h₂ : (1 + Real.sin t) * (1 + Real.cos t) = 5/4)
  (h₃ : (1 - Real.sin t) * (1- Real.cos t) = m/n - Real.sqrt k) (node_rational_and_radical_identification : (m:ℝ)/n=13/4 ∧ k=10) : k + m + n = 27 := by
  have hn : (n:ℝ) ≠ 0 := by exact_mod_cast h₀.2.2.ne'
  have he : 4*(m:ℝ)=13*n := by have := node_rational_and_radical_identification.1; field_simp at this; linarith
  have hei : 4*m=13*n := by exact_mod_cast he
  have hcop : Nat.Coprime n m := (show Nat.Coprime m n from h₁).symm
  have hd : n ∣ 4 := hcop.dvd_of_dvd_mul_right ⟨13, by nlinarith [hei]⟩
  have hb : n ≤ 4 := Nat.le_of_dvd (by norm_num) hd
  interval_cases n <;> norm_num at * <;> omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1995_p7 (k m n : ℕ)
  (t : ℝ)
  (h₀ : 0 < k ∧ 0 < m ∧ 0 < n)
  (h₁ : Nat.gcd m n = 1)
  (h₂ : (1 + Real.sin t) * (1 + Real.cos t) = 5/4)
  (h₃ : (1 - Real.sin t) * (1- Real.cos t) = m/n - Real.sqrt k) : k + m + n = 27 := by
  have node_trigonometric_radical_value : (1-Real.sin t)*(1-Real.cos t)=13/4-Real.sqrt 10 := by
    have hs := Real.sin_sq_add_cos_sq t
    have hp : Real.sin t*Real.cos t ≤ 1/2 := by nlinarith [sq_nonneg (Real.sin t-Real.cos t)]
    have he : (2*(Real.sin t+Real.cos t+1))^2=10 := by nlinarith [h₂]
    have hn : 0 ≤ 2*(Real.sin t+Real.cos t+1) := by nlinarith [h₂]
    have hr : 2*(Real.sin t+Real.cos t+1)=Real.sqrt 10 := by nlinarith [Real.sqrt_nonneg 10, Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 10)]
    nlinarith [h₂]
  have node_rational_and_radical_identification : (m:ℝ)/n=13/4 ∧ k=10 := by
    have hn : (n:ℝ) ≠ 0 := by exact_mod_cast h₀.2.2.ne'
    let q : ℚ := (m:ℚ)/n-13/4
    have hq : (q:ℝ)=(m:ℝ)/n-13/4 := by dsimp [q]; push_cast; rfl
    have hr : Real.sqrt k=Real.sqrt 10+(q:ℝ) := by linarith [h₃]
    have hs := Real.sq_sqrt (by positivity : 0 ≤ (k:ℝ))
    have hs10 := Real.sq_sqrt (by norm_num : (0:ℝ) ≤ 10)
    rw [hr] at hs
    have hi : Irrational (Real.sqrt 10) := by norm_num
    have hq0 : q=0 := by
      by_contra hqne
      have hqr : (q:ℝ) ≠ 0 := by exact_mod_cast hqne
      apply hi.ne_rat (((k:ℚ)-10-q^2)/(2*q))
      push_cast
      apply (eq_div_iff (mul_ne_zero (by norm_num) hqr)).2
      nlinarith only [hs,hs10]
    have hqr : (q:ℝ)=0 := by exact_mod_cast hq0
    constructor
    · linarith
    · have hk : (k:ℝ)=10 := by rw [hqr] at hs; nlinarith only [hs,hs10]
      exact_mod_cast hk
  have node_root : k + m + n = 27 := by
    have hn : (n:ℝ) ≠ 0 := by exact_mod_cast h₀.2.2.ne'
    have he : 4*(m:ℝ)=13*n := by have := node_rational_and_radical_identification.1; field_simp at this; linarith
    have hei : 4*m=13*n := by exact_mod_cast he
    have hcop : Nat.Coprime n m := (show Nat.Coprime m n from h₁).symm
    have hd : n ∣ 4 := hcop.dvd_of_dvd_mul_right ⟨13, by nlinarith [hei]⟩
    have hb : n ≤ 4 := Nat.le_of_dvd (by norm_num) hd
    interval_cases n <;> norm_num at * <;> omega
  exact node_root

#print axioms aime_1995_p7
