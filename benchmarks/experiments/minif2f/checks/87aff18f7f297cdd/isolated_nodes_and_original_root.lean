import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ) (h₀ : 0 < n) : ∀ k ∈ Finset.Icc 1 n, k * Nat.choose n k = n * Nat.choose (n - 1) (k - 1) := by
  intro k hk
  have hn : n ≠ 0 := by omega
  have hkpos : k ≠ 0 := by simp only [Finset.mem_Icc] at hk; omega
  obtain ⟨m, rfl⟩ := Nat.exists_eq_succ_of_ne_zero hn
  obtain ⟨j, rfl⟩ := Nat.exists_eq_succ_of_ne_zero hkpos
  simpa [Nat.mul_comm] using (Nat.succ_mul_choose_eq m j).symm

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (n : ℕ) (h₀ : 0 < n) (node_marked_element_identity : ∀ k ∈ Finset.Icc 1 n, k * Nat.choose n k = n * Nat.choose (n - 1) (k - 1)) : (∑ k ∈ Finset.Icc 1 n, k * Nat.choose n k) = n * 2 ^ (n - 1) := by
  have complete_row : (∑ j ∈ Finset.range n, Nat.choose (n - 1) j) = 2 ^ (n - 1) := by
    have hn : n - 1 + 1 = n := by omega
    simpa [hn] using Nat.sum_range_choose (n - 1)
  calc
    (∑ k ∈ Finset.Icc 1 n, k * Nat.choose n k) = ∑ k ∈ Finset.Icc 1 n, n * Nat.choose (n - 1) (k - 1) := by
      apply Finset.sum_congr rfl
      exact node_marked_element_identity
    _ = n * (∑ j ∈ Finset.range n, Nat.choose (n - 1) j) := by
      rw [Finset.mul_sum]
      apply Finset.sum_bij' (fun k _ => k - 1) (fun j _ => j + 1)
      all_goals
        intro k hk
        simp_all [Finset.mem_Icc, Finset.mem_range] <;> omega
    _ = n * 2 ^ (n - 1) := by rw [complete_row]

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem numbertheory_sumkmulnckeqnmul2pownm1 (n : ℕ) (h₀ : 0 < n) : (∑ k ∈ Finset.Icc 1 n, k * Nat.choose n k) = n * 2 ^ (n - 1) := by
  have node_marked_element_identity : ∀ k ∈ Finset.Icc 1 n, k * Nat.choose n k = n * Nat.choose (n - 1) (k - 1) := by
    intro k hk
    have hn : n ≠ 0 := by omega
    have hkpos : k ≠ 0 := by simp only [Finset.mem_Icc] at hk; omega
    obtain ⟨m, rfl⟩ := Nat.exists_eq_succ_of_ne_zero hn
    obtain ⟨j, rfl⟩ := Nat.exists_eq_succ_of_ne_zero hkpos
    simpa [Nat.mul_comm] using (Nat.succ_mul_choose_eq m j).symm
  have node_root : (∑ k ∈ Finset.Icc 1 n, k * Nat.choose n k) = n * 2 ^ (n - 1) := by
    have complete_row : (∑ j ∈ Finset.range n, Nat.choose (n - 1) j) = 2 ^ (n - 1) := by
      have hn : n - 1 + 1 = n := by omega
      simpa [hn] using Nat.sum_range_choose (n - 1)
    calc
      (∑ k ∈ Finset.Icc 1 n, k * Nat.choose n k) = ∑ k ∈ Finset.Icc 1 n, n * Nat.choose (n - 1) (k - 1) := by
        apply Finset.sum_congr rfl
        exact node_marked_element_identity
      _ = n * (∑ j ∈ Finset.range n, Nat.choose (n - 1) j) := by
        rw [Finset.mul_sum]
        apply Finset.sum_bij' (fun k _ => k - 1) (fun j _ => j + 1)
        all_goals
          intro k hk
          simp_all [Finset.mem_Icc, Finset.mem_range] <;> omega
      _ = n * 2 ^ (n - 1) := by rw [complete_row]
  exact node_root

#print axioms numbertheory_sumkmulnckeqnmul2pownm1
