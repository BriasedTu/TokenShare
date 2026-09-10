import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℚ)
  (m n : ℝ)
  (h₀ : 0 < m ∧ 0 < n)
  (h₁ : m^3 = 2)
  (h₂ : n^3 = 4)
  (h₃ : (a:ℝ) + b * m + c * n = 0) : n=m^2 := by
  apply (pow_left_inj₀ h₀.2.le (sq_nonneg m) (by norm_num : (3:ℕ) ≠ 0)).1
  rw [h₂]
  calc
    (4:ℝ)=(m^3)^2 := by rw [h₁]; norm_num
    _=(m^2)^3 := by ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℚ)
  (m n : ℝ)
  (h₀ : 0 < m ∧ 0 < n)
  (h₁ : m^3 = 2)
  (h₂ : n^3 = 4)
  (h₃ : (a:ℝ) + b * m + c * n = 0) : Irrational m := by
  apply irrational_nrt_of_notint_nrt 3 2 (by simpa using h₁) _ (by norm_num)
  rintro ⟨y, hy⟩
  have hy0 : 0 < y := by exact_mod_cast (hy ▸ h₀.1)
  have hc : y^3=2 := by exact_mod_cast (hy ▸ h₁)
  have hy2 : 2 ≤ y := by
    by_contra hn
    have he : y=1 := by omega
    norm_num [he] at hc
  have hp := pow_le_pow_left₀ (by norm_num : (0:ℤ) ≤ 2) hy2 3
  norm_num [hc] at hp

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℚ)
  (m n : ℝ)
  (h₀ : 0 < m ∧ 0 < n)
  (h₁ : m^3 = 2)
  (h₂ : n^3 = 4)
  (h₃ : (a:ℝ) + b * m + c * n = 0) (node_root_square_relation : n=m^2) (node_cube_root_irrational : Irrational m) : a = 0 ∧ b = 0 ∧ c = 0 := by
  rw [node_root_square_relation] at h₃
  have he : ((a*c-b^2:ℚ):ℝ)*m + (2*c^2-a*b:ℚ)=0 := by
    push_cast
    linear_combination c*m*h₃ - b*h₃ - c^2*h₁
  have hd : a*c-b^2=0 := by
    by_contra hn
    have hnr : ((a*c-b^2:ℚ):ℝ) ≠ 0 := by exact_mod_cast hn
    apply node_cube_root_irrational.ne_rat ((a*b-2*c^2)/(a*c-b^2))
    push_cast
    push_cast at hnr he
    apply (eq_div_iff hnr).2
    nlinarith only [he]
  have hdr : (a:ℝ)*c-b^2=0 := by exact_mod_cast hd
  have hp := congrArg (fun t:ℝ => t*(c:ℝ)) h₃
  have hc : (c:ℝ)=0 := by
    have hs := sq_nonneg ((b:ℝ)+2*c*m)
    have hb0 : (b:ℝ)=0 := by nlinarith only [hp,hdr,hs,sq_nonneg (c*m)]
    have hcm : (c:ℝ)*m=0 := by nlinarith only [hp,hdr,hb0,sq_nonneg (c*m)]
    exact (mul_eq_zero.mp hcm).resolve_right h₀.1.ne'
  have hb : (b:ℝ)=0 := by
    rw [hc] at hdr
    have hz : (b:ℝ)^2=0 := by nlinarith only [hdr]
    exact sq_eq_zero_iff.mp hz
  have ha : (a:ℝ)=0 := by rw [hc,hb] at h₃; simpa using h₃
  exact ⟨by exact_mod_cast ha, by exact_mod_cast hb, by exact_mod_cast hc⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_apbmpcneq0_aeq0anbeq0anceq0 (a b c : ℚ)
  (m n : ℝ)
  (h₀ : 0 < m ∧ 0 < n)
  (h₁ : m^3 = 2)
  (h₂ : n^3 = 4)
  (h₃ : (a:ℝ) + b * m + c * n = 0) : a = 0 ∧ b = 0 ∧ c = 0 := by
  have node_root_square_relation : n=m^2 := by
    apply (pow_left_inj₀ h₀.2.le (sq_nonneg m) (by norm_num : (3:ℕ) ≠ 0)).1
    rw [h₂]
    calc
      (4:ℝ)=(m^3)^2 := by rw [h₁]; norm_num
      _=(m^2)^3 := by ring
  have node_cube_root_irrational : Irrational m := by
    apply irrational_nrt_of_notint_nrt 3 2 (by simpa using h₁) _ (by norm_num)
    rintro ⟨y, hy⟩
    have hy0 : 0 < y := by exact_mod_cast (hy ▸ h₀.1)
    have hc : y^3=2 := by exact_mod_cast (hy ▸ h₁)
    have hy2 : 2 ≤ y := by
      by_contra hn
      have he : y=1 := by omega
      norm_num [he] at hc
    have hp := pow_le_pow_left₀ (by norm_num : (0:ℤ) ≤ 2) hy2 3
    norm_num [hc] at hp
  have node_root : a = 0 ∧ b = 0 ∧ c = 0 := by
    rw [node_root_square_relation] at h₃
    have he : ((a*c-b^2:ℚ):ℝ)*m + (2*c^2-a*b:ℚ)=0 := by
      push_cast
      linear_combination c*m*h₃ - b*h₃ - c^2*h₁
    have hd : a*c-b^2=0 := by
      by_contra hn
      have hnr : ((a*c-b^2:ℚ):ℝ) ≠ 0 := by exact_mod_cast hn
      apply node_cube_root_irrational.ne_rat ((a*b-2*c^2)/(a*c-b^2))
      push_cast
      push_cast at hnr he
      apply (eq_div_iff hnr).2
      nlinarith only [he]
    have hdr : (a:ℝ)*c-b^2=0 := by exact_mod_cast hd
    have hp := congrArg (fun t:ℝ => t*(c:ℝ)) h₃
    have hc : (c:ℝ)=0 := by
      have hs := sq_nonneg ((b:ℝ)+2*c*m)
      have hb0 : (b:ℝ)=0 := by nlinarith only [hp,hdr,hs,sq_nonneg (c*m)]
      have hcm : (c:ℝ)*m=0 := by nlinarith only [hp,hdr,hb0,sq_nonneg (c*m)]
      exact (mul_eq_zero.mp hcm).resolve_right h₀.1.ne'
    have hb : (b:ℝ)=0 := by
      rw [hc] at hdr
      have hz : (b:ℝ)^2=0 := by nlinarith only [hdr]
      exact sq_eq_zero_iff.mp hz
    have ha : (a:ℝ)=0 := by rw [hc,hb] at h₃; simpa using h₃
    exact ⟨by exact_mod_cast ha, by exact_mod_cast hb, by exact_mod_cast hc⟩
  exact node_root

#print axioms algebra_apbmpcneq0_aeq0anbeq0anceq0
