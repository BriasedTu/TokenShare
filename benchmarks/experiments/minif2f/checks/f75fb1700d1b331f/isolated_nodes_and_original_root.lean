import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ n : ℕ, 0 < n → ∀ k : ℕ, ((8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13) ↔ (6 * n < 7 * k ∧ 8 * k < 7 * n) := by
  intro n hn k
  have hd : (0 : ℝ) < n + k := by exact_mod_cast (by omega : 0 < n + k)
  rw [div_lt_div_iff₀ (by norm_num : (0 : ℝ) < 15) hd, div_lt_div_iff₀ hd (by norm_num : (0 : ℝ) < 13)]
  constructor
  · rintro ⟨ha, hb⟩
    constructor
    · have h : (6 : ℝ) * n < 7 * k := by nlinarith
      exact_mod_cast h
    · have h : (8 : ℝ) * k < 7 * n := by nlinarith
      exact_mod_cast h
  · rintro ⟨ha, hb⟩
    have ha' : (6 : ℝ) * n < 7 * k := by exact_mod_cast ha
    have hb' : (8 : ℝ) * k < 7 * n := by exact_mod_cast hb
    constructor <;> nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ n : ℕ, 113 ≤ n → ∃ k l : ℕ, k ≠ l ∧ (6 * n < 7 * k ∧ 8 * k < 7 * n) ∧ (6 * n < 7 * l ∧ 8 * l < 7 * n) := by
  intro n hn
  refine ⟨6 * n / 7 + 1, 6 * n / 7 + 2, ?_⟩
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_integer_interval : ∀ n : ℕ, 0 < n → ∀ k : ℕ, ((8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13) ↔ (6 * n < 7 * k ∧ 8 * k < 7 * n)) (node_two_integers_above_threshold : ∀ n : ℕ, 113 ≤ n → ∃ k l : ℕ, k ≠ l ∧ (6 * n < 7 * k ∧ 8 * k < 7 * n) ∧ (6 * n < 7 * l ∧ 8 * l < 7 * n)) : IsGreatest { n : ℕ | 0 < n ∧ ∃! k : ℕ, (8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13 } 112 := by
  constructor
  · refine ⟨by norm_num, 97, ?_, ?_⟩
    · exact (node_integer_interval 112 (by norm_num) 97).mpr (by norm_num)
    · intro k hk
      have hb := (node_integer_interval 112 (by norm_num) k).mp hk
      omega
  · intro n hn
    change 0 < n ∧ ∃! k : ℕ, (8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13 at hn
    by_contra hg
    obtain ⟨k, l, hkl, hk, hl⟩ := node_two_integers_above_threshold n (by omega)
    obtain ⟨w, hw, hu⟩ := hn.2
    have hkw := hu k ((node_integer_interval n hn.1 k).mpr hk)
    have hlw := hu l ((node_integer_interval n hn.1 l).mpr hl)
    exact hkl (hkw.trans hlw.symm)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem aime_1987_p8 : IsGreatest { n : ℕ | 0 < n ∧ ∃! k : ℕ, (8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13 } 112 := by
  have node_integer_interval : ∀ n : ℕ, 0 < n → ∀ k : ℕ, ((8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13) ↔ (6 * n < 7 * k ∧ 8 * k < 7 * n) := by
    intro n hn k
    have hd : (0 : ℝ) < n + k := by exact_mod_cast (by omega : 0 < n + k)
    rw [div_lt_div_iff₀ (by norm_num : (0 : ℝ) < 15) hd, div_lt_div_iff₀ hd (by norm_num : (0 : ℝ) < 13)]
    constructor
    · rintro ⟨ha, hb⟩
      constructor
      · have h : (6 : ℝ) * n < 7 * k := by nlinarith
        exact_mod_cast h
      · have h : (8 : ℝ) * k < 7 * n := by nlinarith
        exact_mod_cast h
    · rintro ⟨ha, hb⟩
      have ha' : (6 : ℝ) * n < 7 * k := by exact_mod_cast ha
      have hb' : (8 : ℝ) * k < 7 * n := by exact_mod_cast hb
      constructor <;> nlinarith
  have node_two_integers_above_threshold : ∀ n : ℕ, 113 ≤ n → ∃ k l : ℕ, k ≠ l ∧ (6 * n < 7 * k ∧ 8 * k < 7 * n) ∧ (6 * n < 7 * l ∧ 8 * l < 7 * n) := by
    intro n hn
    refine ⟨6 * n / 7 + 1, 6 * n / 7 + 2, ?_⟩
    omega
  have node_root : IsGreatest { n : ℕ | 0 < n ∧ ∃! k : ℕ, (8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13 } 112 := by
    constructor
    · refine ⟨by norm_num, 97, ?_, ?_⟩
      · exact (node_integer_interval 112 (by norm_num) 97).mpr (by norm_num)
      · intro k hk
        have hb := (node_integer_interval 112 (by norm_num) k).mp hk
        omega
    · intro n hn
      change 0 < n ∧ ∃! k : ℕ, (8 : ℝ) / 15 < n / (n + k) ∧ (n : ℝ) / (n + k) < 7 / 13 at hn
      by_contra hg
      obtain ⟨k, l, hkl, hk, hl⟩ := node_two_integers_above_threshold n (by omega)
      obtain ⟨w, hw, hu⟩ := hn.2
      have hkw := hu k ((node_integer_interval n hn.1 k).mpr hk)
      have hlw := hu l ((node_integer_interval n hn.1 l).mpr hl)
      exact hkl (hkw.trans hlw.symm)
  exact node_root

#print axioms aime_1987_p8
