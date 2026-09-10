import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℝ)
  (h₀ : ∀ (x : ℝ), x ∈ S ↔ 0 ≤ x ∧ x ≤ Real.pi ∧ Real.sin (Real.pi / 2 * Real.cos x) = Real.cos (Real.pi / 2 * Real.sin x)) : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin (Real.pi/2*Real.cos x)=Real.cos (Real.pi/2*Real.sin x) ↔ Real.sin x+Real.cos x=1) := by
  intro x hx0 hxpi
  have hp := Real.pi_pos
  have hs0 := Real.sin_nonneg_of_nonneg_of_le_pi hx0 hxpi
  have hs1 := Real.sin_le_one x
  have hc0 := Real.neg_one_le_cos x
  have hc1 := Real.cos_le_one x
  constructor
  · intro he
    rw [← Real.sin_pi_div_two_sub (Real.pi/2*Real.sin x)] at he
    have hi := Real.injOn_sin (show Real.pi/2*Real.cos x ∈ Set.Icc (-(Real.pi/2)) (Real.pi/2) by constructor <;> nlinarith) (show Real.pi/2-Real.pi/2*Real.sin x ∈ Set.Icc (-(Real.pi/2)) (Real.pi/2) by constructor <;> nlinarith) he
    nlinarith
  · intro he
    have hi : Real.pi/2*Real.cos x = Real.pi/2-Real.pi/2*Real.sin x := by nlinarith
    rw [hi, Real.sin_pi_div_two_sub]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℝ)
  (h₀ : ∀ (x : ℝ), x ∈ S ↔ 0 ≤ x ∧ x ≤ Real.pi ∧ Real.sin (Real.pi / 2 * Real.cos x) = Real.cos (Real.pi / 2 * Real.sin x)) : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin x+Real.cos x=1 ↔ x=0 ∨ x=Real.pi/2) := by
  intro x hx0 hxpi
  constructor
  · intro he
    have hs := Real.sin_sq_add_cos_sq x
    have hz : Real.sin x*Real.cos x=0 := by nlinarith
    rcases mul_eq_zero.mp hz with hs0 | hc0
    · left
      apply Real.injOn_cos ⟨hx0,hxpi⟩ ⟨le_rfl, Real.pi_pos.le⟩
      simp only [Real.cos_zero]
      linarith
    · right
      apply Real.injOn_cos ⟨hx0,hxpi⟩ ⟨by positivity, by linarith [Real.pi_pos]⟩
      simpa only [Real.cos_pi_div_two] using hc0
  · rintro (rfl | rfl) <;> norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℝ)
  (h₀ : ∀ (x : ℝ), x ∈ S ↔ 0 ≤ x ∧ x ≤ Real.pi ∧ Real.sin (Real.pi / 2 * Real.cos x) = Real.cos (Real.pi / 2 * Real.sin x)) (node_angle_equivalence : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin (Real.pi/2*Real.cos x)=Real.cos (Real.pi/2*Real.sin x) ↔ Real.sin x+Real.cos x=1)) (node_angle_classification : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin x+Real.cos x=1 ↔ x=0 ∨ x=Real.pi/2)) : S.card = 2 := by
  classical
  have he : S={0,Real.pi/2} := by
    ext x
    rw [h₀]
    simp only [Finset.mem_insert, Finset.mem_singleton]
    constructor
    · rintro ⟨hx0,hxpi,hx⟩
      exact (node_angle_classification x hx0 hxpi).1 ((node_angle_equivalence x hx0 hxpi).1 hx)
    · rintro (rfl | rfl)
      · norm_num [Real.pi_pos.le]
      · refine ⟨by positivity, by linarith [Real.pi_pos], ?_⟩
        norm_num
  rw [he]
  have hp : (0:ℝ) ≠ Real.pi/2 := by linarith [Real.pi_pos]
  simp [hp]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2021_p19 (S : Finset ℝ)
  (h₀ : ∀ (x : ℝ), x ∈ S ↔ 0 ≤ x ∧ x ≤ Real.pi ∧ Real.sin (Real.pi / 2 * Real.cos x) = Real.cos (Real.pi / 2 * Real.sin x)) : S.card = 2 := by
  have node_angle_equivalence : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin (Real.pi/2*Real.cos x)=Real.cos (Real.pi/2*Real.sin x) ↔ Real.sin x+Real.cos x=1) := by
    intro x hx0 hxpi
    have hp := Real.pi_pos
    have hs0 := Real.sin_nonneg_of_nonneg_of_le_pi hx0 hxpi
    have hs1 := Real.sin_le_one x
    have hc0 := Real.neg_one_le_cos x
    have hc1 := Real.cos_le_one x
    constructor
    · intro he
      rw [← Real.sin_pi_div_two_sub (Real.pi/2*Real.sin x)] at he
      have hi := Real.injOn_sin (show Real.pi/2*Real.cos x ∈ Set.Icc (-(Real.pi/2)) (Real.pi/2) by constructor <;> nlinarith) (show Real.pi/2-Real.pi/2*Real.sin x ∈ Set.Icc (-(Real.pi/2)) (Real.pi/2) by constructor <;> nlinarith) he
      nlinarith
    · intro he
      have hi : Real.pi/2*Real.cos x = Real.pi/2-Real.pi/2*Real.sin x := by nlinarith
      rw [hi, Real.sin_pi_div_two_sub]
  have node_angle_classification : ∀ x : ℝ, 0 ≤ x → x ≤ Real.pi → (Real.sin x+Real.cos x=1 ↔ x=0 ∨ x=Real.pi/2) := by
    intro x hx0 hxpi
    constructor
    · intro he
      have hs := Real.sin_sq_add_cos_sq x
      have hz : Real.sin x*Real.cos x=0 := by nlinarith
      rcases mul_eq_zero.mp hz with hs0 | hc0
      · left
        apply Real.injOn_cos ⟨hx0,hxpi⟩ ⟨le_rfl, Real.pi_pos.le⟩
        simp only [Real.cos_zero]
        linarith
      · right
        apply Real.injOn_cos ⟨hx0,hxpi⟩ ⟨by positivity, by linarith [Real.pi_pos]⟩
        simpa only [Real.cos_pi_div_two] using hc0
    · rintro (rfl | rfl) <;> norm_num
  have node_root : S.card = 2 := by
    classical
    have he : S={0,Real.pi/2} := by
      ext x
      rw [h₀]
      simp only [Finset.mem_insert, Finset.mem_singleton]
      constructor
      · rintro ⟨hx0,hxpi,hx⟩
        exact (node_angle_classification x hx0 hxpi).1 ((node_angle_equivalence x hx0 hxpi).1 hx)
      · rintro (rfl | rfl)
        · norm_num [Real.pi_pos.le]
        · refine ⟨by positivity, by linarith [Real.pi_pos], ?_⟩
          norm_num
    rw [he]
    have hp : (0:ℝ) ≠ Real.pi/2 := by linarith [Real.pi_pos]
    simp [hp]
  exact node_root

#print axioms amc12a_2021_p19
