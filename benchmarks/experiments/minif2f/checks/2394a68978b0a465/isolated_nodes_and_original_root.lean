import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d s : ℝ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 < d)
  (h₁ : s = a / (a + b + d) + b / (a + b + c) + c / (b + c + d) + d / (a + c + d)) : 1 < s := by
  rcases h₀ with ⟨ha, hb, hc, hd⟩
  have hT : 0 < a + b + c + d := by linarith
  have hA : a / (a + b + c + d) < a / (a + b + d) := div_lt_div_of_pos_left ha (by linarith) (by linarith)
  have hB : b / (a + b + c + d) < b / (a + b + c) := div_lt_div_of_pos_left hb (by linarith) (by linarith)
  have hC : c / (a + b + c + d) < c / (b + c + d) := div_lt_div_of_pos_left hc (by linarith) (by linarith)
  have hD : d / (a + b + c + d) < d / (a + c + d) := div_lt_div_of_pos_left hd (by linarith) (by linarith)
  have ht : a / (a + b + c + d) + b / (a + b + c + d) + c / (a + b + c + d) + d / (a + b + c + d) = 1 := by
    field_simp
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d s : ℝ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 < d)
  (h₁ : s = a / (a + b + d) + b / (a + b + c) + c / (b + c + d) + d / (a + c + d)) : s < 2 := by
  rcases h₀ with ⟨ha, hb, hc, hd⟩
  have hA : a / (a + b + d) < a / (a + d) := div_lt_div_of_pos_left ha (by linarith) (by linarith)
  have hD : d / (a + c + d) < d / (a + d) := div_lt_div_of_pos_left hd (by linarith) (by linarith)
  have hB : b / (a + b + c) < b / (b + c) := div_lt_div_of_pos_left hb (by linarith) (by linarith)
  have hC : c / (b + c + d) < c / (b + c) := div_lt_div_of_pos_left hc (by linarith) (by linarith)
  have hAD : a / (a + d) + d / (a + d) = 1 := by field_simp
  have hBC : b / (b + c) + c / (b + c) = 1 := by field_simp
  linarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b c d s : ℝ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 < d)
  (h₁ : s = a / (a + b + d) + b / (a + b + c) + c / (b + c + d) + d / (a + c + d)) (node_strict_lower_bound : 1 < s) (node_strict_upper_bound : s < 2) : 1 < s ∧ s < 2 := by
  exact ⟨node_strict_lower_bound, node_strict_upper_bound⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem imo_1974_p5 (a b c d s : ℝ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c ∧ 0 < d)
  (h₁ : s = a / (a + b + d) + b / (a + b + c) + c / (b + c + d) + d / (a + c + d)) : 1 < s ∧ s < 2 := by
  have node_strict_lower_bound : 1 < s := by
    rcases h₀ with ⟨ha, hb, hc, hd⟩
    have hT : 0 < a + b + c + d := by linarith
    have hA : a / (a + b + c + d) < a / (a + b + d) := div_lt_div_of_pos_left ha (by linarith) (by linarith)
    have hB : b / (a + b + c + d) < b / (a + b + c) := div_lt_div_of_pos_left hb (by linarith) (by linarith)
    have hC : c / (a + b + c + d) < c / (b + c + d) := div_lt_div_of_pos_left hc (by linarith) (by linarith)
    have hD : d / (a + b + c + d) < d / (a + c + d) := div_lt_div_of_pos_left hd (by linarith) (by linarith)
    have ht : a / (a + b + c + d) + b / (a + b + c + d) + c / (a + b + c + d) + d / (a + b + c + d) = 1 := by
      field_simp
    linarith
  have node_strict_upper_bound : s < 2 := by
    rcases h₀ with ⟨ha, hb, hc, hd⟩
    have hA : a / (a + b + d) < a / (a + d) := div_lt_div_of_pos_left ha (by linarith) (by linarith)
    have hD : d / (a + c + d) < d / (a + d) := div_lt_div_of_pos_left hd (by linarith) (by linarith)
    have hB : b / (a + b + c) < b / (b + c) := div_lt_div_of_pos_left hb (by linarith) (by linarith)
    have hC : c / (b + c + d) < c / (b + c) := div_lt_div_of_pos_left hc (by linarith) (by linarith)
    have hAD : a / (a + d) + d / (a + d) = 1 := by field_simp
    have hBC : b / (b + c) + c / (b + c) = 1 := by field_simp
    linarith
  have node_root : 1 < s ∧ s < 2 := by
    exact ⟨node_strict_lower_bound, node_strict_upper_bound⟩
  exact node_root

#print axioms imo_1974_p5
