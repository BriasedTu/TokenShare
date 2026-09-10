import Mathlib
set_option linter.unusedVariables false

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℕ)
  (h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ (↑n + (1000 : ℝ)) / 70 = Int.floor (Real.sqrt n)) : ∀ n ∈ S, n=400 ∨ n=470 ∨ n=2290 ∨ n=2360 ∨ n=2430 ∨ n=2500 := by
  intro n hn
  obtain ⟨hn0, he⟩ := (h₀ n).mp hn
  let m : ℤ := Int.floor (Real.sqrt n)
  have hm : (n:ℝ)+1000=70*m := by dsimp [m]; linarith
  have hm0 : (0:ℝ) ≤ m := by positivity
  have hml : (m:ℝ) ≤ Real.sqrt n := Int.floor_le _
  have hmu : Real.sqrt n < (m:ℝ)+1 := Int.lt_floor_add_one _
  have hs := Real.sq_sqrt (by positivity : 0 ≤ (n:ℝ))
  have hl : (m:ℝ)^2 ≤ n := by nlinarith
  have hu : (n:ℝ) < ((m:ℝ)+1)^2 := by nlinarith [Real.sqrt_nonneg (n:ℝ)]
  have hm20 : (20:ℝ) ≤ m := by nlinarith
  have hm50 : (m:ℝ) ≤ 50 := by nlinarith
  have mi20 : 20 ≤ m := by exact_mod_cast hm20
  have mi50 : m ≤ 50 := by exact_mod_cast hm50
  have hmi : (n:ℤ)+1000=70*m := by exact_mod_cast hm
  have hli : m^2 ≤ (n:ℤ) := by exact_mod_cast hl
  have hui : (n:ℤ) < (m+1)^2 := by exact_mod_cast hu
  interval_cases m <;> norm_num at hmi hli hui <;> omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℕ)
  (h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ (↑n + (1000 : ℝ)) / 70 = Int.floor (Real.sqrt n)) : 400 ∈ S ∧ 470 ∈ S ∧ 2290 ∈ S ∧ 2360 ∈ S ∧ 2430 ∈ S ∧ 2500 ∈ S := by
  have hf : ∀ (n : ℕ) (m : ℤ), 0 < n → (n:ℝ)+1000=70*m → (m:ℝ)^2 ≤ n → (n:ℝ)<((m:ℝ)+1)^2 → 0 ≤ m → n ∈ S := by
    intro n m hn he hl hu hm
    apply (h₀ n).2
    refine ⟨hn, ?_⟩
    have hm0 : (0:ℝ) ≤ m := by exact_mod_cast hm
    have hf : Int.floor (Real.sqrt n)=m := by
      apply Int.floor_eq_iff.2
      constructor
      · exact (Real.le_sqrt hm0 (by positivity)).2 hl
      · exact (Real.sqrt_lt (by positivity) (by linarith)).2 hu
    rw [hf]
    linarith
  exact ⟨hf 400 20 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 470 21 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2290 47 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2360 48 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2430 49 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2500 50 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num)⟩

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℕ)
  (h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ (↑n + (1000 : ℝ)) / 70 = Int.floor (Real.sqrt n)) (node_necessary_memberships : ∀ n ∈ S, n=400 ∨ n=470 ∨ n=2290 ∨ n=2360 ∨ n=2430 ∨ n=2500) (node_sufficient_memberships : 400 ∈ S ∧ 470 ∈ S ∧ 2290 ∈ S ∧ 2360 ∈ S ∧ 2430 ∈ S ∧ 2500 ∈ S) : S.card = 6 := by
  have he : S={400,470,2290,2360,2430,2500} := by
    ext n
    simp only [Finset.mem_insert, Finset.mem_singleton]
    constructor
    · exact node_necessary_memberships n
    · rintro (rfl | rfl | rfl | rfl | rfl | rfl) <;> tauto
  rw [he]
  norm_num

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12b_2020_p21 (S : Finset ℕ)
  (h₀ : ∀ (n : ℕ), n ∈ S ↔ 0 < n ∧ (↑n + (1000 : ℝ)) / 70 = Int.floor (Real.sqrt n)) : S.card = 6 := by
  have node_necessary_memberships : ∀ n ∈ S, n=400 ∨ n=470 ∨ n=2290 ∨ n=2360 ∨ n=2430 ∨ n=2500 := by
    intro n hn
    obtain ⟨hn0, he⟩ := (h₀ n).mp hn
    let m : ℤ := Int.floor (Real.sqrt n)
    have hm : (n:ℝ)+1000=70*m := by dsimp [m]; linarith
    have hm0 : (0:ℝ) ≤ m := by positivity
    have hml : (m:ℝ) ≤ Real.sqrt n := Int.floor_le _
    have hmu : Real.sqrt n < (m:ℝ)+1 := Int.lt_floor_add_one _
    have hs := Real.sq_sqrt (by positivity : 0 ≤ (n:ℝ))
    have hl : (m:ℝ)^2 ≤ n := by nlinarith
    have hu : (n:ℝ) < ((m:ℝ)+1)^2 := by nlinarith [Real.sqrt_nonneg (n:ℝ)]
    have hm20 : (20:ℝ) ≤ m := by nlinarith
    have hm50 : (m:ℝ) ≤ 50 := by nlinarith
    have mi20 : 20 ≤ m := by exact_mod_cast hm20
    have mi50 : m ≤ 50 := by exact_mod_cast hm50
    have hmi : (n:ℤ)+1000=70*m := by exact_mod_cast hm
    have hli : m^2 ≤ (n:ℤ) := by exact_mod_cast hl
    have hui : (n:ℤ) < (m+1)^2 := by exact_mod_cast hu
    interval_cases m <;> norm_num at hmi hli hui <;> omega
  have node_sufficient_memberships : 400 ∈ S ∧ 470 ∈ S ∧ 2290 ∈ S ∧ 2360 ∈ S ∧ 2430 ∈ S ∧ 2500 ∈ S := by
    have hf : ∀ (n : ℕ) (m : ℤ), 0 < n → (n:ℝ)+1000=70*m → (m:ℝ)^2 ≤ n → (n:ℝ)<((m:ℝ)+1)^2 → 0 ≤ m → n ∈ S := by
      intro n m hn he hl hu hm
      apply (h₀ n).2
      refine ⟨hn, ?_⟩
      have hm0 : (0:ℝ) ≤ m := by exact_mod_cast hm
      have hf : Int.floor (Real.sqrt n)=m := by
        apply Int.floor_eq_iff.2
        constructor
        · exact (Real.le_sqrt hm0 (by positivity)).2 hl
        · exact (Real.sqrt_lt (by positivity) (by linarith)).2 hu
      rw [hf]
      linarith
    exact ⟨hf 400 20 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 470 21 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2290 47 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2360 48 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2430 49 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num), hf 2500 50 (by norm_num) (by norm_num) (by norm_num) (by norm_num) (by norm_num)⟩
  have node_root : S.card = 6 := by
    have he : S={400,470,2290,2360,2430,2500} := by
      ext n
      simp only [Finset.mem_insert, Finset.mem_singleton]
      constructor
      · exact node_necessary_memberships n
      · rintro (rfl | rfl | rfl | rfl | rfl | rfl) <;> tauto
    rw [he]
    norm_num
  exact node_root

#print axioms amc12b_2020_p21
