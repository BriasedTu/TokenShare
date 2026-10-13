import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℚ) (h₀ : (x ^ 2 + y ^ 2).den = 1) : x.den ^ 2 ∣ y.den ^ 2 := by
  have he : x ^ 2 = (x ^ 2 + y ^ 2) + (-(y ^ 2)) := by ring
  have hd := Rat.add_den_dvd (x ^ 2 + y ^ 2) (-(y ^ 2))
  rw [← he, h₀] at hd
  simpa [Rat.den_pow] using hd

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℚ) (h₀ : (x ^ 2 + y ^ 2).den = 1) : y.den ^ 2 ∣ x.den ^ 2 := by
  have he : y ^ 2 = (x ^ 2 + y ^ 2) + (-(x ^ 2)) := by ring
  have hd := Rat.add_den_dvd (x ^ 2 + y ^ 2) (-(x ^ 2))
  rw [← he, h₀] at hd
  simpa [Rat.den_pow] using hd

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x y : ℚ) (h₀ : (x ^ 2 + y ^ 2).den = 1) (node_first_denominator_divides : x.den ^ 2 ∣ y.den ^ 2) (node_second_denominator_divides : y.den ^ 2 ∣ x.den ^ 2) : x.den = y.den := by
  have he := Nat.dvd_antisymm node_first_denominator_divides node_second_denominator_divides
  nlinarith

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_xsqpysqintdenomeq (x y : ℚ) (h₀ : (x ^ 2 + y ^ 2).den = 1) : x.den = y.den := by
  have node_first_denominator_divides : x.den ^ 2 ∣ y.den ^ 2 := by
    have he : x ^ 2 = (x ^ 2 + y ^ 2) + (-(y ^ 2)) := by ring
    have hd := Rat.add_den_dvd (x ^ 2 + y ^ 2) (-(y ^ 2))
    rw [← he, h₀] at hd
    simpa [Rat.den_pow] using hd
  have node_second_denominator_divides : y.den ^ 2 ∣ x.den ^ 2 := by
    have he : y ^ 2 = (x ^ 2 + y ^ 2) + (-(x ^ 2)) := by ring
    have hd := Rat.add_den_dvd (x ^ 2 + y ^ 2) (-(x ^ 2))
    rw [← he, h₀] at hd
    simpa [Rat.den_pow] using hd
  have node_root : x.den = y.den := by
    have he := Nat.dvd_antisymm node_first_denominator_divides node_second_denominator_divides
    nlinarith
  exact node_root

#print axioms numbertheory_xsqpysqintdenomeq
