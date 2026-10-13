import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n k : ℕ) (h₀ : 0 < n ∧ 0 < k) (h₀ : (n : ℝ) / k < 6)
  (h₁ : (5 : ℝ) < n / k) : ∀ a b : ℕ, (5 : ℝ) < a / b → (a : ℝ) / b < 6 → 22 ≤ a * b := by
  intro a b hlo hhi
  have hb : 0 < b := by
    by_contra h
    have hb0 : b = 0 := by omega
    norm_num [hb0] at hlo
  have hbR : (0 : ℝ) < b := by exact_mod_cast hb
  have hloR := (lt_div_iff₀ hbR).mp hlo
  have hhiR := (div_lt_iff₀ hbR).mp hhi
  have alo : 5 * b < a := by exact_mod_cast hloR
  have ahi : a < 6 * b := by exact_mod_cast hhiR
  have blow : 2 ≤ b := by omega
  have alow : 11 ≤ a := by omega
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n k : ℕ) (h₀ : 0 < n ∧ 0 < k) (h₀ : (n : ℝ) / k < 6)
  (h₁ : (5 : ℝ) < n / k) : ∃ a b : ℕ, n = Nat.gcd n k * a ∧ k = Nat.gcd n k * b ∧ 0 < Nat.gcd n k ∧ (n : ℝ) / k = (a : ℝ) / b := by
  have hk : 0 < k := by
    by_contra h
    have hk0 : k = 0 := by omega
    norm_num [hk0] at h₁
  have hg := Nat.gcd_pos_of_pos_right n hk
  obtain ⟨a, ha⟩ := Nat.gcd_dvd_left n k
  obtain ⟨b, hb⟩ := Nat.gcd_dvd_right n k
  refine ⟨a, b, ha, hb, hg, ?_⟩
  have hgR : (Nat.gcd n k : ℝ) ≠ 0 := by exact_mod_cast Nat.ne_of_gt hg
  calc
    (n : ℝ) / k = ((Nat.gcd n k : ℝ) * a) / ((Nat.gcd n k : ℝ) * b) := by
      exact congrArg₂ (fun x y : ℝ => x / y) (by exact_mod_cast ha) (by exact_mod_cast hb)
    _ = (a : ℝ) / b := mul_div_mul_left _ _ hgR

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n k : ℕ) (h₀ : 0 < n ∧ 0 < k) (h₀ : (n : ℝ) / k < 6)
  (h₁ : (5 : ℝ) < n / k) (node_integer_ratio_bound : ∀ a b : ℕ, (5 : ℝ) < a / b → (a : ℝ) / b < 6 → 22 ≤ a * b) (node_gcd_reduction_data : ∃ a b : ℕ, n = Nat.gcd n k * a ∧ k = Nat.gcd n k * b ∧ 0 < Nat.gcd n k ∧ (n : ℝ) / k = (a : ℝ) / b) : 22 ≤ Nat.lcm n k / Nat.gcd n k := by
  obtain ⟨a, b, ha, hb, hg, hr⟩ := node_gcd_reduction_data
  have hab : 22 ≤ a * b := node_integer_ratio_bound a b (by simpa [hr] using h₁) (by simpa [hr] using h₀)
  let g := Nat.gcd n k
  change n = g * a at ha
  change k = g * b at hb
  change 0 < g at hg
  have ident : g * Nat.lcm n k = g * (g * (a * b)) := by
    calc
      g * Nat.lcm n k = n * k := Nat.gcd_mul_lcm n k
      _ = g * (g * (a * b)) := by rw [ha, hb]; ring
  have hl := Nat.eq_of_mul_eq_mul_left hg ident
  change 22 ≤ Nat.lcm n k / g
  rw [hl, Nat.mul_div_right (a * b) hg]
  exact hab

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_numbertheory_530 (n k : ℕ) (h₀ : 0 < n ∧ 0 < k) (h₀ : (n : ℝ) / k < 6)
  (h₁ : (5 : ℝ) < n / k) : 22 ≤ Nat.lcm n k / Nat.gcd n k := by
  have node_integer_ratio_bound : ∀ a b : ℕ, (5 : ℝ) < a / b → (a : ℝ) / b < 6 → 22 ≤ a * b := by
    intro a b hlo hhi
    have hb : 0 < b := by
      by_contra h
      have hb0 : b = 0 := by omega
      norm_num [hb0] at hlo
    have hbR : (0 : ℝ) < b := by exact_mod_cast hb
    have hloR := (lt_div_iff₀ hbR).mp hlo
    have hhiR := (div_lt_iff₀ hbR).mp hhi
    have alo : 5 * b < a := by exact_mod_cast hloR
    have ahi : a < 6 * b := by exact_mod_cast hhiR
    have blow : 2 ≤ b := by omega
    have alow : 11 ≤ a := by omega
    nlinarith
  have node_gcd_reduction_data : ∃ a b : ℕ, n = Nat.gcd n k * a ∧ k = Nat.gcd n k * b ∧ 0 < Nat.gcd n k ∧ (n : ℝ) / k = (a : ℝ) / b := by
    have hk : 0 < k := by
      by_contra h
      have hk0 : k = 0 := by omega
      norm_num [hk0] at h₁
    have hg := Nat.gcd_pos_of_pos_right n hk
    obtain ⟨a, ha⟩ := Nat.gcd_dvd_left n k
    obtain ⟨b, hb⟩ := Nat.gcd_dvd_right n k
    refine ⟨a, b, ha, hb, hg, ?_⟩
    have hgR : (Nat.gcd n k : ℝ) ≠ 0 := by exact_mod_cast Nat.ne_of_gt hg
    calc
      (n : ℝ) / k = ((Nat.gcd n k : ℝ) * a) / ((Nat.gcd n k : ℝ) * b) := by
        exact congrArg₂ (fun x y : ℝ => x / y) (by exact_mod_cast ha) (by exact_mod_cast hb)
      _ = (a : ℝ) / b := mul_div_mul_left _ _ hgR
  have node_root : 22 ≤ Nat.lcm n k / Nat.gcd n k := by
    obtain ⟨a, b, ha, hb, hg, hr⟩ := node_gcd_reduction_data
    have hab : 22 ≤ a * b := node_integer_ratio_bound a b (by simpa [hr] using h₁) (by simpa [hr] using h₀)
    let g := Nat.gcd n k
    change n = g * a at ha
    change k = g * b at hb
    change 0 < g at hg
    have ident : g * Nat.lcm n k = g * (g * (a * b)) := by
      calc
        g * Nat.lcm n k = n * k := Nat.gcd_mul_lcm n k
        _ = g * (g * (a * b)) := by rw [ha, hb]; ring
    have hl := Nat.eq_of_mul_eq_mul_left hg ident
    change 22 ≤ Nat.lcm n k / g
    rw [hl, Nat.mul_div_right (a * b) hg]
    exact hab
  exact node_root

#print axioms mathd_numbertheory_530
