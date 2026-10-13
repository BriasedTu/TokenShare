import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ n : ℕ, (∏ k ∈ (Finset.range (2*n)).filter (fun k => ¬Even k), k) = ∏ i ∈ Finset.range n, (2*i+1) := by
  intro n
  symm
  apply Finset.prod_bij (fun i _ => 2*i+1)
  · intro i hi
    simp only [Finset.mem_range] at hi
    simp only [Finset.mem_filter, Finset.mem_range, Nat.even_iff]
    omega
  · intro i hi j hj he
    omega
  · intro k hk
    simp only [Finset.mem_filter, Finset.mem_range, Nat.even_iff] at hk
    refine ⟨k/2, ?_, ?_⟩
    · simp only [Finset.mem_range]; omega
    · omega
  · intro i hi
    rfl

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example : ∀ n : ℕ, (2*n)! = (2^n*(n !))*(∏ i ∈ Finset.range n, (2*i+1)) := by
  intro n
  induction n with
  | zero => norm_num
  | succ n ih =>
    rw [show 2*(n+1)=2*n+1+1 by omega, Nat.factorial_succ, Nat.factorial_succ, ih, Finset.prod_range_succ, Nat.factorial_succ, pow_succ]
    ring

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (node_odd_filter_reindex : ∀ n : ℕ, (∏ k ∈ (Finset.range (2*n)).filter (fun k => ¬Even k), k) = ∏ i ∈ Finset.range n, (2*i+1)) (node_factorial_pair_partition : ∀ n : ℕ, (2*n)! = (2^n*(n !))*(∏ i ∈ Finset.range n, (2*i+1))) : Finset.prod (Finset.filter (λ x => ¬ Even x) (Finset.range 10000)) (id : ℕ → ℕ) = (10000!) / ((2^5000) * (5000!)) := by
  have h1 := node_odd_filter_reindex 5000
  have h2 := node_factorial_pair_partition 5000
  simp only [show 2*5000=10000 by rfl] at h1 h2
  change (∏ k ∈ (Finset.range 10000).filter (fun k => ¬Even k), k) = (10000)!/(2^5000*(5000 !))
  rw [h1,h2]
  symm
  exact Nat.mul_div_right _ (by positivity)

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12_2001_p5 : Finset.prod (Finset.filter (λ x => ¬ Even x) (Finset.range 10000)) (id : ℕ → ℕ) = (10000!) / ((2^5000) * (5000!)) := by
  have node_odd_filter_reindex : ∀ n : ℕ, (∏ k ∈ (Finset.range (2*n)).filter (fun k => ¬Even k), k) = ∏ i ∈ Finset.range n, (2*i+1) := by
    intro n
    symm
    apply Finset.prod_bij (fun i _ => 2*i+1)
    · intro i hi
      simp only [Finset.mem_range] at hi
      simp only [Finset.mem_filter, Finset.mem_range, Nat.even_iff]
      omega
    · intro i hi j hj he
      omega
    · intro k hk
      simp only [Finset.mem_filter, Finset.mem_range, Nat.even_iff] at hk
      refine ⟨k/2, ?_, ?_⟩
      · simp only [Finset.mem_range]; omega
      · omega
    · intro i hi
      rfl
  have node_factorial_pair_partition : ∀ n : ℕ, (2*n)! = (2^n*(n !))*(∏ i ∈ Finset.range n, (2*i+1)) := by
    intro n
    induction n with
    | zero => norm_num
    | succ n ih =>
      rw [show 2*(n+1)=2*n+1+1 by omega, Nat.factorial_succ, Nat.factorial_succ, ih, Finset.prod_range_succ, Nat.factorial_succ, pow_succ]
      ring
  have node_root : Finset.prod (Finset.filter (λ x => ¬ Even x) (Finset.range 10000)) (id : ℕ → ℕ) = (10000!) / ((2^5000) * (5000!)) := by
    have h1 := node_odd_filter_reindex 5000
    have h2 := node_factorial_pair_partition 5000
    simp only [show 2*5000=10000 by rfl] at h1 h2
    change (∏ k ∈ (Finset.range 10000).filter (fun k => ¬Even k), k) = (10000)!/(2^5000*(5000 !))
    rw [h1,h2]
    symm
    exact Nat.mul_div_right _ (by positivity)
  exact node_root

#print axioms amc12_2001_p5
