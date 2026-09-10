import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d : ℕ)
  (h₀ : a * b * c * d = Nat.factorial 8)
  (h₁ : a * b + a + b = 524)
  (h₂ : b * c + b + c = 146)
  (h₃ : c * d + c + d = 104) : b+1 ∣ 21 := by
  have h1 : (a+1)*(b+1)=525 := by nlinarith
  have h2 : (b+1)*(c+1)=147 := by nlinarith
  have d1 : b+1 ∣ 525 := ⟨a+1, by nlinarith [h1]⟩
  have d2 : b+1 ∣ 147 := ⟨c+1, h2.symm⟩
  have hd := Nat.dvd_gcd d1 d2
  norm_num at hd
  exact hd

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d : ℕ)
  (h₀ : a * b * c * d = Nat.factorial 8)
  (h₁ : a * b + a + b = 524)
  (h₂ : b * c + b + c = 146)
  (h₃ : c * d + c + d = 104) (node_common_divisor : b+1 ∣ 21) : b=6 ∨ b=20 := by
  have hb : b ≤ 20 := by have := Nat.le_of_dvd (by norm_num : 0 < 21) node_common_divisor; omega
  interval_cases b <;> norm_num at *
  have hc : c=48 := by omega
  rw [hc] at h₃
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d : ℕ)
  (h₀ : a * b * c * d = Nat.factorial 8)
  (h₁ : a * b + a + b = 524)
  (h₂ : b * c + b + c = 146)
  (h₃ : c * d + c + d = 104) (node_candidate_middle_values : b=6 ∨ b=20) : ↑a - ↑d = (10 : ℤ) := by
  rcases node_candidate_middle_values with hb | hb
  · rw [hb] at h₀ h₁ h₂
    have ha : a=74 := by omega
    have hc : c=20 := by omega
    rw [hc] at h₃
    have hd : d=4 := by omega
    norm_num [ha, hc, hd] at h₀
  · rw [hb] at h₁ h₂
    have ha : a=24 := by omega
    have hc : c=6 := by omega
    rw [hc] at h₃
    have hd : d=14 := by omega
    norm_num [ha, hd]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12_2001_p21 (a b c d : ℕ)
  (h₀ : a * b * c * d = Nat.factorial 8)
  (h₁ : a * b + a + b = 524)
  (h₂ : b * c + b + c = 146)
  (h₃ : c * d + c + d = 104) : ↑a - ↑d = (10 : ℤ) := by
  have node_common_divisor : b+1 ∣ 21 := by
    have h1 : (a+1)*(b+1)=525 := by nlinarith
    have h2 : (b+1)*(c+1)=147 := by nlinarith
    have d1 : b+1 ∣ 525 := ⟨a+1, by nlinarith [h1]⟩
    have d2 : b+1 ∣ 147 := ⟨c+1, h2.symm⟩
    have hd := Nat.dvd_gcd d1 d2
    norm_num at hd
    exact hd
  have node_candidate_middle_values : b=6 ∨ b=20 := by
    have hb : b ≤ 20 := by have := Nat.le_of_dvd (by norm_num : 0 < 21) node_common_divisor; omega
    interval_cases b <;> norm_num at *
    have hc : c=48 := by omega
    rw [hc] at h₃
    omega
  have node_root : ↑a - ↑d = (10 : ℤ) := by
    rcases node_candidate_middle_values with hb | hb
    · rw [hb] at h₀ h₁ h₂
      have ha : a=74 := by omega
      have hc : c=20 := by omega
      rw [hc] at h₃
      have hd : d=4 := by omega
      norm_num [ha, hc, hd] at h₀
    · rw [hb] at h₁ h₂
      have ha : a=24 := by omega
      have hc : c=6 := by omega
      rw [hc] at h₃
      have hd : d=14 := by omega
      norm_num [ha, hd]
  exact node_root

#print axioms amc12_2001_p21
