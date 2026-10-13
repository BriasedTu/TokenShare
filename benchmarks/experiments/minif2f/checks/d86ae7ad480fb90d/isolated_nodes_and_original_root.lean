import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) : abs (a + b) / (1 + abs (a + b)) ≤ (abs a + abs b) / (1 + (abs a + abs b)) := by
  apply (div_le_div_iff₀ (by positivity) (by positivity)).2
  nlinarith [abs_add_le a b]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) : (abs a + abs b) / (1 + (abs a + abs b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b) := by
  have ha : abs a / (1 + (abs a + abs b)) ≤ abs a / (1 + abs a) :=
    div_le_div_of_nonneg_left (abs_nonneg a) (by positivity) (by linarith [abs_nonneg b])
  have hb : abs b / (1 + (abs a + abs b)) ≤ abs b / (1 + abs b) :=
    div_le_div_of_nonneg_left (abs_nonneg b) (by positivity) (by linarith [abs_nonneg a])
  rw [add_div]
  exact add_le_add ha hb

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a b : ℝ) (node_triangle_majorant : abs (a + b) / (1 + abs (a + b)) ≤ (abs a + abs b) / (1 + (abs a + abs b))) (node_separate_denominators : (abs a + abs b) / (1 + (abs a + abs b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b)) : abs (a + b) / (1 + abs (a + b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b) := by
  exact le_trans node_triangle_majorant node_separate_denominators

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem algebra_absapbon1pabsapbleqsumabsaon1pabsa (a b : ℝ) : abs (a + b) / (1 + abs (a + b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b) := by
  have node_triangle_majorant : abs (a + b) / (1 + abs (a + b)) ≤ (abs a + abs b) / (1 + (abs a + abs b)) := by
    apply (div_le_div_iff₀ (by positivity) (by positivity)).2
    nlinarith [abs_add_le a b]
  have node_separate_denominators : (abs a + abs b) / (1 + (abs a + abs b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b) := by
    have ha : abs a / (1 + (abs a + abs b)) ≤ abs a / (1 + abs a) :=
      div_le_div_of_nonneg_left (abs_nonneg a) (by positivity) (by linarith [abs_nonneg b])
    have hb : abs b / (1 + (abs a + abs b)) ≤ abs b / (1 + abs b) :=
      div_le_div_of_nonneg_left (abs_nonneg b) (by positivity) (by linarith [abs_nonneg a])
    rw [add_div]
    exact add_le_add ha hb
  have node_root : abs (a + b) / (1 + abs (a + b)) ≤ abs a / (1 + abs a) + abs b / (1 + abs b) := by
    exact le_trans node_triangle_majorant node_separate_denominators
  exact node_root

#print axioms algebra_absapbon1pabsapbleqsumabsaon1pabsa
