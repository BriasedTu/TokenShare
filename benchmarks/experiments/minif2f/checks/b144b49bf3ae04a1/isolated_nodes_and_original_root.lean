import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℚ) (h₀ : a 1 = 1) (h₁ : a 2 = 3 / 7)
  (h₂ : ∀ n, a (n + 2) = a n * a (n + 1) / (2 * a n - a (n + 1))) : ∀ n : ℕ, a (n + 1) = 3 / (4 * (n : ℚ) + 3) ∧ a (n + 2) = 3 / (4 * (n : ℚ) + 7) := by
  intro n
  induction n with
  | zero => norm_num [h₀, h₁]
  | succ n ih =>
      constructor
      · convert ih.2 using 1 ; push_cast ; congr 1 ; ring
      · have hr := h₂ (n + 1)
        rw [ih.1, ih.2] at hr
        have h3 : 4 * (n : ℚ) + 3 ≠ 0 := by positivity
        have h7 : 4 * (n : ℚ) + 7 ≠ 0 := by positivity
        have h11 : 4 * (n : ℚ) + 11 ≠ 0 := by positivity
        change a (n + 1 + 2) = _
        rw [hr]
        push_cast
        field_simp
        ; ring_nf
        ; field_simp
        ; ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (a : ℕ → ℚ) (h₀ : a 1 = 1) (h₁ : a 2 = 3 / 7)
  (h₂ : ∀ n, a (n + 2) = a n * a (n + 1) / (2 * a n - a (n + 1))) (node_rational_closed_form : ∀ n : ℕ, a (n + 1) = 3 / (4 * (n : ℚ) + 3) ∧ a (n + 2) = 3 / (4 * (n : ℚ) + 7)) : ↑(a 2019).den + (a 2019).num = 8078 := by
  have hv := (node_rational_closed_form 2018).1
  norm_num at hv
  rw [hv]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2019_p9 (a : ℕ → ℚ) (h₀ : a 1 = 1) (h₁ : a 2 = 3 / 7)
  (h₂ : ∀ n, a (n + 2) = a n * a (n + 1) / (2 * a n - a (n + 1))) : ↑(a 2019).den + (a 2019).num = 8078 := by
  have node_rational_closed_form : ∀ n : ℕ, a (n + 1) = 3 / (4 * (n : ℚ) + 3) ∧ a (n + 2) = 3 / (4 * (n : ℚ) + 7) := by
    intro n
    induction n with
    | zero => norm_num [h₀, h₁]
    | succ n ih =>
        constructor
        · convert ih.2 using 1 ; push_cast ; congr 1 ; ring
        · have hr := h₂ (n + 1)
          rw [ih.1, ih.2] at hr
          have h3 : 4 * (n : ℚ) + 3 ≠ 0 := by positivity
          have h7 : 4 * (n : ℚ) + 7 ≠ 0 := by positivity
          have h11 : 4 * (n : ℚ) + 11 ≠ 0 := by positivity
          change a (n + 1 + 2) = _
          rw [hr]
          push_cast
          field_simp
          ; ring_nf
          ; field_simp
          ; ring
  have node_root : ↑(a 2019).den + (a 2019).num = 8078 := by
    have hv := (node_rational_closed_form 2018).1
    norm_num at hv
    rw [hv]
    norm_num
  exact node_root

#print axioms amc12a_2019_p9
