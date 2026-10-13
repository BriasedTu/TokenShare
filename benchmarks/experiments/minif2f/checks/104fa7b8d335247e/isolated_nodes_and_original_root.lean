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
  (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = x^3 + a * x^2 + b * x + c)
  (h₁ : f⁻¹' {0} = {Real.cos (2 * Real.pi / 7), Real.cos (4 * Real.pi / 7), Real.cos (6 * Real.pi / 7)}) : ∀ k : ℕ, 1 ≤ k → k ≤ 3 → 8*(Real.cos (2*k*Real.pi/7))^3+4*(Real.cos (2*k*Real.pi/7))^2-4*Real.cos (2*k*Real.pi/7)-1=0 := by
  intro k hk1 hk3
  let t : ℝ := 2*k*Real.pi/7
  have ht0 : 0 < t := by dsimp [t]; positivity
  have htpi : t ≤ Real.pi := by
    dsimp [t]
    have : (k:ℝ) ≤ 3 := by exact_mod_cast hk3
    nlinarith [Real.pi_pos]
  have hn : Real.cos t ≠ 1 := by intro he; have hx := Real.injOn_cos ⟨ht0.le,htpi⟩ ⟨le_rfl,Real.pi_pos.le⟩ (by simpa using he); linarith
  have h4 : Real.cos (4*t)=Real.cos (3*t) := by
    rw [show 4*t=(k:ℝ)*(2*Real.pi)-3*t by dsimp [t]; ring]
    exact Real.cos_nat_mul_two_pi_sub (3*t) k
  have h2 := Real.cos_two_mul t
  have h22 := Real.cos_two_mul (2*t)
  have h3 := Real.cos_three_mul t
  have hz : (Real.cos t-1)*(8*(Real.cos t)^3+4*(Real.cos t)^2-4*Real.cos t-1)=0 := by
    rw [show 2*(2*t)=4*t by ring, h4] at h22
    nlinarith only [h2,h22,h3]
  exact (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = x^3 + a * x^2 + b * x + c)
  (h₁ : f⁻¹' {0} = {Real.cos (2 * Real.pi / 7), Real.cos (4 * Real.pi / 7), Real.cos (6 * Real.pi / 7)}) : Real.cos (2*Real.pi/7) ≠ Real.cos (4*Real.pi/7) ∧ Real.cos (2*Real.pi/7) ≠ Real.cos (6*Real.pi/7) ∧ Real.cos (4*Real.pi/7) ≠ Real.cos (6*Real.pi/7) := by
  have hp := Real.pi_pos
  have h1 : Real.cos (4*Real.pi/7) < Real.cos (2*Real.pi/7) := Real.strictAntiOn_cos (by constructor <;> linarith) (by constructor <;> linarith) (by linarith)
  have h2 : Real.cos (6*Real.pi/7) < Real.cos (4*Real.pi/7) := Real.strictAntiOn_cos (by constructor <;> linarith) (by constructor <;> linarith) (by linarith)
  exact ⟨ne_of_gt h1,ne_of_gt (h2.trans h1),ne_of_gt h2⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c : ℝ)
  (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = x^3 + a * x^2 + b * x + c)
  (h₁ : f⁻¹' {0} = {Real.cos (2 * Real.pi / 7), Real.cos (4 * Real.pi / 7), Real.cos (6 * Real.pi / 7)}) (node_seventh_cosine_cubic : ∀ k : ℕ, 1 ≤ k → k ≤ 3 → 8*(Real.cos (2*k*Real.pi/7))^3+4*(Real.cos (2*k*Real.pi/7))^2-4*Real.cos (2*k*Real.pi/7)-1=0) (node_distinct_cosine_roots : Real.cos (2*Real.pi/7) ≠ Real.cos (4*Real.pi/7) ∧ Real.cos (2*Real.pi/7) ≠ Real.cos (6*Real.pi/7) ∧ Real.cos (4*Real.pi/7) ≠ Real.cos (6*Real.pi/7)) : a * b * c = 1 / 32 := by
  let u := Real.cos (2*Real.pi/7)
  let v := Real.cos (4*Real.pi/7)
  let w := Real.cos (6*Real.pi/7)
  have f1 : f u=0 := by
    have hx : u ∈ f⁻¹' {0} := by rw [h₁]; simp [u]
    exact hx
  have f2 : f v=0 := by
    have hx : v ∈ f⁻¹' {0} := by rw [h₁]; simp [v]
    exact hx
  have f3 : f w=0 := by
    have hx : w ∈ f⁻¹' {0} := by rw [h₁]; simp [w]
    exact hx
  have p1 := node_seventh_cosine_cubic 1 (by norm_num) (by norm_num)
  have p2 := node_seventh_cosine_cubic 2 (by norm_num) (by norm_num)
  have p3 := node_seventh_cosine_cubic 3 (by norm_num) (by norm_num)
  norm_num at p1 p2 p3
  have e1 : (a-1/2)*u^2+(b+1/2)*u+(c+1/8)=0 := by dsimp [u]; nlinarith [h₀ u]
  have e2 : (a-1/2)*v^2+(b+1/2)*v+(c+1/8)=0 := by dsimp [v]; nlinarith [h₀ v]
  have e3 : (a-1/2)*w^2+(b+1/2)*w+(c+1/8)=0 := by dsimp [w]; nlinarith [h₀ w]
  have d1 : u-v ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.1
  have d2 : u-w ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.2.1
  have d3 : v-w ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.2.2
  have hz : (a-1/2)*((u-v)*(u-w)*(v-w))=0 := by linear_combination (v-w)*e1+(w-u)*e2+(u-v)*e3
  have ha : a=1/2 := by have := (mul_eq_zero.mp hz).resolve_right (mul_ne_zero (mul_ne_zero d1 d2) d3); linarith
  have hz2 : (b+1/2)*(u-v)=0 := by rw [ha] at e1 e2; nlinarith only [e1,e2]
  have hb : b= -1/2 := by have := (mul_eq_zero.mp hz2).resolve_right d1; linarith
  have hc : c= -1/8 := by rw [ha,hb] at e1; nlinarith only [e1]
  rw [ha,hb,hc]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2021_p22 (a b c : ℝ)
  (f : ℝ → ℝ)
  (h₀ : ∀ x, f x = x^3 + a * x^2 + b * x + c)
  (h₁ : f⁻¹' {0} = {Real.cos (2 * Real.pi / 7), Real.cos (4 * Real.pi / 7), Real.cos (6 * Real.pi / 7)}) : a * b * c = 1 / 32 := by
  have node_seventh_cosine_cubic : ∀ k : ℕ, 1 ≤ k → k ≤ 3 → 8*(Real.cos (2*k*Real.pi/7))^3+4*(Real.cos (2*k*Real.pi/7))^2-4*Real.cos (2*k*Real.pi/7)-1=0 := by
    intro k hk1 hk3
    let t : ℝ := 2*k*Real.pi/7
    have ht0 : 0 < t := by dsimp [t]; positivity
    have htpi : t ≤ Real.pi := by
      dsimp [t]
      have : (k:ℝ) ≤ 3 := by exact_mod_cast hk3
      nlinarith [Real.pi_pos]
    have hn : Real.cos t ≠ 1 := by intro he; have hx := Real.injOn_cos ⟨ht0.le,htpi⟩ ⟨le_rfl,Real.pi_pos.le⟩ (by simpa using he); linarith
    have h4 : Real.cos (4*t)=Real.cos (3*t) := by
      rw [show 4*t=(k:ℝ)*(2*Real.pi)-3*t by dsimp [t]; ring]
      exact Real.cos_nat_mul_two_pi_sub (3*t) k
    have h2 := Real.cos_two_mul t
    have h22 := Real.cos_two_mul (2*t)
    have h3 := Real.cos_three_mul t
    have hz : (Real.cos t-1)*(8*(Real.cos t)^3+4*(Real.cos t)^2-4*Real.cos t-1)=0 := by
      rw [show 2*(2*t)=4*t by ring, h4] at h22
      nlinarith only [h2,h22,h3]
    exact (mul_eq_zero.mp hz).resolve_left (sub_ne_zero.mpr hn)
  have node_distinct_cosine_roots : Real.cos (2*Real.pi/7) ≠ Real.cos (4*Real.pi/7) ∧ Real.cos (2*Real.pi/7) ≠ Real.cos (6*Real.pi/7) ∧ Real.cos (4*Real.pi/7) ≠ Real.cos (6*Real.pi/7) := by
    have hp := Real.pi_pos
    have h1 : Real.cos (4*Real.pi/7) < Real.cos (2*Real.pi/7) := Real.strictAntiOn_cos (by constructor <;> linarith) (by constructor <;> linarith) (by linarith)
    have h2 : Real.cos (6*Real.pi/7) < Real.cos (4*Real.pi/7) := Real.strictAntiOn_cos (by constructor <;> linarith) (by constructor <;> linarith) (by linarith)
    exact ⟨ne_of_gt h1,ne_of_gt (h2.trans h1),ne_of_gt h2⟩
  have node_root : a * b * c = 1 / 32 := by
    let u := Real.cos (2*Real.pi/7)
    let v := Real.cos (4*Real.pi/7)
    let w := Real.cos (6*Real.pi/7)
    have f1 : f u=0 := by
      have hx : u ∈ f⁻¹' {0} := by rw [h₁]; simp [u]
      exact hx
    have f2 : f v=0 := by
      have hx : v ∈ f⁻¹' {0} := by rw [h₁]; simp [v]
      exact hx
    have f3 : f w=0 := by
      have hx : w ∈ f⁻¹' {0} := by rw [h₁]; simp [w]
      exact hx
    have p1 := node_seventh_cosine_cubic 1 (by norm_num) (by norm_num)
    have p2 := node_seventh_cosine_cubic 2 (by norm_num) (by norm_num)
    have p3 := node_seventh_cosine_cubic 3 (by norm_num) (by norm_num)
    norm_num at p1 p2 p3
    have e1 : (a-1/2)*u^2+(b+1/2)*u+(c+1/8)=0 := by dsimp [u]; nlinarith [h₀ u]
    have e2 : (a-1/2)*v^2+(b+1/2)*v+(c+1/8)=0 := by dsimp [v]; nlinarith [h₀ v]
    have e3 : (a-1/2)*w^2+(b+1/2)*w+(c+1/8)=0 := by dsimp [w]; nlinarith [h₀ w]
    have d1 : u-v ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.1
    have d2 : u-w ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.2.1
    have d3 : v-w ≠ 0 := sub_ne_zero.mpr node_distinct_cosine_roots.2.2
    have hz : (a-1/2)*((u-v)*(u-w)*(v-w))=0 := by linear_combination (v-w)*e1+(w-u)*e2+(u-v)*e3
    have ha : a=1/2 := by have := (mul_eq_zero.mp hz).resolve_right (mul_ne_zero (mul_ne_zero d1 d2) d3); linarith
    have hz2 : (b+1/2)*(u-v)=0 := by rw [ha] at e1 e2; nlinarith only [e1,e2]
    have hb : b= -1/2 := by have := (mul_eq_zero.mp hz2).resolve_right d1; linarith
    have hc : c= -1/8 := by rw [ha,hb] at e1; nlinarith only [e1]
    rw [ha,hb,hc]
    norm_num
  exact node_root

#print axioms amc12a_2021_p22
