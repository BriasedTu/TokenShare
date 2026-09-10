import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ) : x ^ 5 % 11 = 0 ∨ x ^ 5 % 11 = 1 ∨ x ^ 5 % 11 = 10 := by
  have hlo : 0 ≤ x % 11 := Int.emod_nonneg _ (by norm_num)
  have hhi : x % 11 < 11 := Int.emod_lt_of_pos _ (by norm_num)
  interval_cases hx : x % 11 <;> norm_num [pow_succ, Int.mul_emod, hx]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ) : (y ^ 2 + 4) % 11 = 2 ∨ (y ^ 2 + 4) % 11 = 4 ∨ (y ^ 2 + 4) % 11 = 5 ∨ (y ^ 2 + 4) % 11 = 7 ∨ (y ^ 2 + 4) % 11 = 8 ∨ (y ^ 2 + 4) % 11 = 9 := by
  have hlo : 0 ≤ y % 11 := Int.emod_nonneg _ (by norm_num)
  have hhi : y % 11 < 11 := Int.emod_lt_of_pos _ (by norm_num)
  interval_cases hy : y % 11 <;> norm_num [Int.add_emod, pow_succ, Int.mul_emod, hy]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℤ) (node_fifth_power_residues : x ^ 5 % 11 = 0 ∨ x ^ 5 % 11 = 1 ∨ x ^ 5 % 11 = 10) (node_shifted_square_residues : (y ^ 2 + 4) % 11 = 2 ∨ (y ^ 2 + 4) % 11 = 4 ∨ (y ^ 2 + 4) % 11 = 5 ∨ (y ^ 2 + 4) % 11 = 7 ∨ (y ^ 2 + 4) % 11 = 8 ∨ (y ^ 2 + 4) % 11 = 9) : x^5 ≠ y^2 + 4 := by
  intro he
  rw [he] at node_fifth_power_residues
  omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_x5neqy2p4 (x y : ℤ) : x^5 ≠ y^2 + 4 := by
  have node_fifth_power_residues : x ^ 5 % 11 = 0 ∨ x ^ 5 % 11 = 1 ∨ x ^ 5 % 11 = 10 := by
    have hlo : 0 ≤ x % 11 := Int.emod_nonneg _ (by norm_num)
    have hhi : x % 11 < 11 := Int.emod_lt_of_pos _ (by norm_num)
    interval_cases hx : x % 11 <;> norm_num [pow_succ, Int.mul_emod, hx]
  have node_shifted_square_residues : (y ^ 2 + 4) % 11 = 2 ∨ (y ^ 2 + 4) % 11 = 4 ∨ (y ^ 2 + 4) % 11 = 5 ∨ (y ^ 2 + 4) % 11 = 7 ∨ (y ^ 2 + 4) % 11 = 8 ∨ (y ^ 2 + 4) % 11 = 9 := by
    have hlo : 0 ≤ y % 11 := Int.emod_nonneg _ (by norm_num)
    have hhi : y % 11 < 11 := Int.emod_lt_of_pos _ (by norm_num)
    interval_cases hy : y % 11 <;> norm_num [Int.add_emod, pow_succ, Int.mul_emod, hy]
  have node_root : x^5 ≠ y^2 + 4 := by
    intro he
    rw [he] at node_fifth_power_residues
    omega
  exact node_root

#print axioms numbertheory_x5neqy2p4
