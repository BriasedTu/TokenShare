import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : NNReal) (u : ℕ → NNReal) (h₀ : ∀ n, u (n + 1) = NNReal.sqrt (x + u n))
  (h₁ : Filter.Tendsto u Filter.atTop (𝓝 9)) : Filter.Tendsto (fun n ↦ NNReal.sqrt (x + u n)) Filter.atTop (𝓝 (NNReal.sqrt (x + 9))) := by
  have hc : Continuous (fun y : NNReal ↦ NNReal.sqrt (x + y)) := by continuity
  exact hc.continuousAt.tendsto.comp h₁

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (x : NNReal) (u : ℕ → NNReal) (h₀ : ∀ n, u (n + 1) = NNReal.sqrt (x + u n))
  (h₁ : Filter.Tendsto u Filter.atTop (𝓝 9)) (node_continuous_recurrence_limit : Filter.Tendsto (fun n ↦ NNReal.sqrt (x + u n)) Filter.atTop (𝓝 (NNReal.sqrt (x + 9)))) : 9 = NNReal.sqrt (x + 9) := by
  have hs : Filter.Tendsto (fun n ↦ u (n + 1)) Filter.atTop (𝓝 9) := h₁.comp (Filter.tendsto_add_atTop_nat 1)
  have heq : (fun n ↦ u (n + 1)) = (fun n ↦ NNReal.sqrt (x + u n)) := funext h₀
  rw [heq] at hs
  exact tendsto_nhds_unique hs node_continuous_recurrence_limit

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem mathd_algebra_31 (x : NNReal) (u : ℕ → NNReal) (h₀ : ∀ n, u (n + 1) = NNReal.sqrt (x + u n))
  (h₁ : Filter.Tendsto u Filter.atTop (𝓝 9)) : 9 = NNReal.sqrt (x + 9) := by
  have node_continuous_recurrence_limit : Filter.Tendsto (fun n ↦ NNReal.sqrt (x + u n)) Filter.atTop (𝓝 (NNReal.sqrt (x + 9))) := by
    have hc : Continuous (fun y : NNReal ↦ NNReal.sqrt (x + y)) := by continuity
    exact hc.continuousAt.tendsto.comp h₁
  have node_root : 9 = NNReal.sqrt (x + 9) := by
    have hs : Filter.Tendsto (fun n ↦ u (n + 1)) Filter.atTop (𝓝 9) := h₁.comp (Filter.tendsto_add_atTop_nat 1)
    have heq : (fun n ↦ u (n + 1)) = (fun n ↦ NNReal.sqrt (x + u n)) := funext h₀
    rw [heq] at hs
    exact tendsto_nhds_unique hs node_continuous_recurrence_limit
  exact node_root

#print axioms mathd_algebra_31
