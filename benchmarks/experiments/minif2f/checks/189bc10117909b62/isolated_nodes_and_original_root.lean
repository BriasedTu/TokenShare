import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y z : ℝ)
  (a : ℕ → ℝ)
  (h₀ : 0 < a 0 ∧ 0 < a 4 ∧ 0 < a 8)
  (h₁ : a 1 < 0 ∧ a 2 < 0)
  (h₂ : a 3 < 0 ∧ a 5 < 0)
  (h₃ : a 6 < 0 ∧ a 7 < 0)
  (h₄ : 0 < a 0 + a 1 + a 2)
  (h₅ : 0 < a 3 + a 4 + a 5)
  (h₆ : 0 < a 6 + a 7 + a 8)
  (h₇ : a 0 * x + a 1 * y + a 2 * z = 0)
  (h₈ : a 3 * x + a 4 * y + a 5 * z = 0)
  (h₉ : a 6 * x + a 7 * y + a 8 * z = 0) : ∀ u v w : ℝ, a 0 * u + a 1 * v + a 2 * w = 0 → a 3 * u + a 4 * v + a 5 * w = 0 → a 6 * u + a 7 * v + a 8 * w = 0 → u ≤ 0 ∧ v ≤ 0 ∧ w ≤ 0 := by
  have row : ∀ α β γ u v w : ℝ, 0 < α + β + γ → β < 0 → γ < 0 → v ≤ u → w ≤ u → α * u + β * v + γ * w = 0 → u ≤ 0 := by
    intro α β γ u v w hsum hβ hγ hv hw he
    by_contra hn
    have hu : 0 < u := by linarith
    have hp := mul_pos hsum hu
    have hq := mul_nonneg (neg_nonneg.mpr hβ.le) (sub_nonneg.mpr hv)
    have hr := mul_nonneg (neg_nonneg.mpr hγ.le) (sub_nonneg.mpr hw)
    nlinarith only [he, hp, hq, hr]
  intro u v w e0 e1 e2
  have hu : v ≤ u → w ≤ u → u ≤ 0 := by
    intro hv hw
    exact row (a 0) (a 1) (a 2) u v w h₄ h₁.1 h₁.2 hv hw e0
  have hv : u ≤ v → w ≤ v → v ≤ 0 := by
    intro hu hw
    apply row (a 4) (a 3) (a 5) v u w (by linarith [h₅]) h₂.1 h₂.2 hu hw
    nlinarith only [e1]
  have hw : u ≤ w → v ≤ w → w ≤ 0 := by
    intro hu hv
    apply row (a 8) (a 6) (a 7) w u v (by linarith [h₆]) h₃.1 h₃.2 hu hv
    nlinarith only [e2]
  rcases le_total u v with huv | hvu
  · rcases le_total v w with hvw | hwv
    · have h := hw (huv.trans hvw) hvw
      exact ⟨huv.trans (hvw.trans h), hvw.trans h, h⟩
    · have h := hv huv hwv
      exact ⟨huv.trans h, h, hwv.trans h⟩
  · rcases le_total u w with huw | hwu
    · have h := hw huw (hvu.trans huw)
      exact ⟨huw.trans h, hvu.trans (huw.trans h), h⟩
    · have h := hu hvu hwu
      exact ⟨h, hvu.trans h, hwu.trans h⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y z : ℝ)
  (a : ℕ → ℝ)
  (h₀ : 0 < a 0 ∧ 0 < a 4 ∧ 0 < a 8)
  (h₁ : a 1 < 0 ∧ a 2 < 0)
  (h₂ : a 3 < 0 ∧ a 5 < 0)
  (h₃ : a 6 < 0 ∧ a 7 < 0)
  (h₄ : 0 < a 0 + a 1 + a 2)
  (h₅ : 0 < a 3 + a 4 + a 5)
  (h₆ : 0 < a 6 + a 7 + a 8)
  (h₇ : a 0 * x + a 1 * y + a 2 * z = 0)
  (h₈ : a 3 * x + a 4 * y + a 5 * z = 0)
  (h₉ : a 6 * x + a 7 * y + a 8 * z = 0) (node_maximum_principle : ∀ u v w : ℝ, a 0 * u + a 1 * v + a 2 * w = 0 → a 3 * u + a 4 * v + a 5 * w = 0 → a 6 * u + a 7 * v + a 8 * w = 0 → u ≤ 0 ∧ v ≤ 0 ∧ w ≤ 0) : x = 0 ∧ y = 0 ∧ z = 0 := by
  have pos := node_maximum_principle x y z h₇ h₈ h₉
  have neg := node_maximum_principle (-x) (-y) (-z) (by nlinarith [h₇]) (by nlinarith [h₈]) (by nlinarith [h₉])
  constructor
  · linarith [pos.1, neg.1]
  · constructor <;> linarith [pos.2.1, pos.2.2, neg.2.1, neg.2.2]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1965_p2 (x y z : ℝ)
  (a : ℕ → ℝ)
  (h₀ : 0 < a 0 ∧ 0 < a 4 ∧ 0 < a 8)
  (h₁ : a 1 < 0 ∧ a 2 < 0)
  (h₂ : a 3 < 0 ∧ a 5 < 0)
  (h₃ : a 6 < 0 ∧ a 7 < 0)
  (h₄ : 0 < a 0 + a 1 + a 2)
  (h₅ : 0 < a 3 + a 4 + a 5)
  (h₆ : 0 < a 6 + a 7 + a 8)
  (h₇ : a 0 * x + a 1 * y + a 2 * z = 0)
  (h₈ : a 3 * x + a 4 * y + a 5 * z = 0)
  (h₉ : a 6 * x + a 7 * y + a 8 * z = 0) : x = 0 ∧ y = 0 ∧ z = 0 := by
  have node_maximum_principle : ∀ u v w : ℝ, a 0 * u + a 1 * v + a 2 * w = 0 → a 3 * u + a 4 * v + a 5 * w = 0 → a 6 * u + a 7 * v + a 8 * w = 0 → u ≤ 0 ∧ v ≤ 0 ∧ w ≤ 0 := by
    have row : ∀ α β γ u v w : ℝ, 0 < α + β + γ → β < 0 → γ < 0 → v ≤ u → w ≤ u → α * u + β * v + γ * w = 0 → u ≤ 0 := by
      intro α β γ u v w hsum hβ hγ hv hw he
      by_contra hn
      have hu : 0 < u := by linarith
      have hp := mul_pos hsum hu
      have hq := mul_nonneg (neg_nonneg.mpr hβ.le) (sub_nonneg.mpr hv)
      have hr := mul_nonneg (neg_nonneg.mpr hγ.le) (sub_nonneg.mpr hw)
      nlinarith only [he, hp, hq, hr]
    intro u v w e0 e1 e2
    have hu : v ≤ u → w ≤ u → u ≤ 0 := by
      intro hv hw
      exact row (a 0) (a 1) (a 2) u v w h₄ h₁.1 h₁.2 hv hw e0
    have hv : u ≤ v → w ≤ v → v ≤ 0 := by
      intro hu hw
      apply row (a 4) (a 3) (a 5) v u w (by linarith [h₅]) h₂.1 h₂.2 hu hw
      nlinarith only [e1]
    have hw : u ≤ w → v ≤ w → w ≤ 0 := by
      intro hu hv
      apply row (a 8) (a 6) (a 7) w u v (by linarith [h₆]) h₃.1 h₃.2 hu hv
      nlinarith only [e2]
    rcases le_total u v with huv | hvu
    · rcases le_total v w with hvw | hwv
      · have h := hw (huv.trans hvw) hvw
        exact ⟨huv.trans (hvw.trans h), hvw.trans h, h⟩
      · have h := hv huv hwv
        exact ⟨huv.trans h, h, hwv.trans h⟩
    · rcases le_total u w with huw | hwu
      · have h := hw huw (hvu.trans huw)
        exact ⟨huw.trans h, hvu.trans (huw.trans h), h⟩
      · have h := hu hvu hwu
        exact ⟨h, hvu.trans h, hwu.trans h⟩
  have node_root : x = 0 ∧ y = 0 ∧ z = 0 := by
    have pos := node_maximum_principle x y z h₇ h₈ h₉
    have neg := node_maximum_principle (-x) (-y) (-z) (by nlinarith [h₇]) (by nlinarith [h₈]) (by nlinarith [h₉])
    constructor
    · linarith [pos.1, neg.1]
    · constructor <;> linarith [pos.2.1, pos.2.2, neg.2.1, neg.2.2]
  exact node_root

#print axioms imo_1965_p2
