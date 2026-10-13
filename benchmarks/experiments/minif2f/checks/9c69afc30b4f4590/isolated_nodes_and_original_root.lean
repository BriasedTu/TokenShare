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
  (h₀ : ∀ (k : ℕ), k ∈ S ↔ 0 < k ∧ ((k * k) : ℕ) ∣ (∏ i ∈ (Finset.Icc 1 9), i !)) : ∀ k : ℕ, 0 < k → (k*k ∣ (∏ i ∈ Finset.Icc 1 9, i !) ↔ k ∣ 2^15*3^6*5^2*7) := by
  have factorial_prime_product : (∏ i ∈ Finset.Icc 1 9, i !) = 2^30*3^13*5^5*7^3 := by
    norm_num [Finset.prod_Icc_succ_top, Nat.factorial]
  intro k hk
  rw [factorial_prime_product, ← pow_two]
  rw [← Nat.factorization_le_iff_dvd (pow_ne_zero _ hk.ne') (by positivity), ← Nat.factorization_le_iff_dvd hk.ne' (by positivity), Nat.factorization_pow]
  have hf : ∀ e2 e3 e5 e7 : ℕ, (2^e2*3^e3*5^e5*7^e7).factorization = Finsupp.single 2 e2+Finsupp.single 3 e3+Finsupp.single 5 e5+Finsupp.single 7 e7 := by
    intro e2 e3 e5 e7
    rw [Nat.factorization_mul (by positivity) (by positivity), Nat.factorization_mul (by positivity) (by positivity), Nat.factorization_mul (by positivity) (by positivity)]
    simp only [Nat.factorization_pow, Nat.Prime.factorization (by norm_num : Nat.Prime 2), Nat.Prime.factorization (by norm_num : Nat.Prime 3), Nat.Prime.factorization (by norm_num : Nat.Prime 5), Nat.Prime.factorization (by norm_num : Nat.Prime 7), Finsupp.smul_single, smul_eq_mul, mul_one]
  have hP := hf 30 13 5 3
  have hQ : (2^15*3^6*5^2*7:ℕ).factorization = Finsupp.single 2 15+Finsupp.single 3 6+Finsupp.single 5 2+Finsupp.single 7 1 := by simpa only [pow_one] using hf 15 6 2 1
  rw [hP,hQ]
  simp only [Finsupp.le_def, Finsupp.smul_apply, Finsupp.add_apply, Finsupp.single_apply, smul_eq_mul]
  constructor
  · intro h p
    have hp := h p
    split_ifs at hp ⊢ <;> omega
  · intro h p
    have hp := h p
    split_ifs at hp ⊢ <;> omega

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
example (S : Finset ℕ)
  (h₀ : ∀ (k : ℕ), k ∈ S ↔ 0 < k ∧ ((k * k) : ℕ) ∣ (∏ i ∈ (Finset.Icc 1 9), i !)) (node_square_divisor_reduction : ∀ k : ℕ, 0 < k → (k*k ∣ (∏ i ∈ Finset.Icc 1 9, i !) ↔ k ∣ 2^15*3^6*5^2*7)) : S.card = 672 := by
  have he : S=Nat.divisors (2^15*3^6*5^2*7) := by
    ext k
    rw [h₀,Nat.mem_divisors]
    constructor
    · rintro ⟨hk,hd⟩
      exact ⟨(node_square_divisor_reduction k hk).1 hd,by positivity⟩
    · rintro ⟨hd,hn⟩
      have hk : 0 < k := Nat.pos_of_dvd_of_pos hd (by positivity)
      exact ⟨hk,(node_square_divisor_reduction k hk).2 hd⟩
  rw [he]
  have hpcount : ∀ p e : ℕ, Nat.Prime p → (p^e).divisors.card=e+1 := by
    intro p e hp
    cases e with
    | zero => simp
    | succ e =>
      rw [Nat.card_divisors (pow_ne_zero _ hp.ne_zero),Nat.primeFactors_pow_succ]
      simp only [hp.primeFactors,Finset.prod_singleton,Nat.factorization_pow_self hp]
  rw [Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15*3^6*5^2) 7 by norm_num),Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15*3^6) (5^2) by norm_num),Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15) (3^6) by norm_num)]
  rw [hpcount 2 15 (by norm_num),hpcount 3 6 (by norm_num),hpcount 5 2 (by norm_num)]
  have h7 := hpcount 7 1 (by norm_num)
  norm_num at h7
  rw [h7]
  

set_option autoImplicit false
set_option maxHeartbeats 0
open BigOperators
open Real
open Nat
open Topology
open Rat
theorem amc12a_2003_p23 (S : Finset ℕ)
  (h₀ : ∀ (k : ℕ), k ∈ S ↔ 0 < k ∧ ((k * k) : ℕ) ∣ (∏ i ∈ (Finset.Icc 1 9), i !)) : S.card = 672 := by
  have node_square_divisor_reduction : ∀ k : ℕ, 0 < k → (k*k ∣ (∏ i ∈ Finset.Icc 1 9, i !) ↔ k ∣ 2^15*3^6*5^2*7) := by
    have factorial_prime_product : (∏ i ∈ Finset.Icc 1 9, i !) = 2^30*3^13*5^5*7^3 := by
      norm_num [Finset.prod_Icc_succ_top, Nat.factorial]
    intro k hk
    rw [factorial_prime_product, ← pow_two]
    rw [← Nat.factorization_le_iff_dvd (pow_ne_zero _ hk.ne') (by positivity), ← Nat.factorization_le_iff_dvd hk.ne' (by positivity), Nat.factorization_pow]
    have hf : ∀ e2 e3 e5 e7 : ℕ, (2^e2*3^e3*5^e5*7^e7).factorization = Finsupp.single 2 e2+Finsupp.single 3 e3+Finsupp.single 5 e5+Finsupp.single 7 e7 := by
      intro e2 e3 e5 e7
      rw [Nat.factorization_mul (by positivity) (by positivity), Nat.factorization_mul (by positivity) (by positivity), Nat.factorization_mul (by positivity) (by positivity)]
      simp only [Nat.factorization_pow, Nat.Prime.factorization (by norm_num : Nat.Prime 2), Nat.Prime.factorization (by norm_num : Nat.Prime 3), Nat.Prime.factorization (by norm_num : Nat.Prime 5), Nat.Prime.factorization (by norm_num : Nat.Prime 7), Finsupp.smul_single, smul_eq_mul, mul_one]
    have hP := hf 30 13 5 3
    have hQ : (2^15*3^6*5^2*7:ℕ).factorization = Finsupp.single 2 15+Finsupp.single 3 6+Finsupp.single 5 2+Finsupp.single 7 1 := by simpa only [pow_one] using hf 15 6 2 1
    rw [hP,hQ]
    simp only [Finsupp.le_def, Finsupp.smul_apply, Finsupp.add_apply, Finsupp.single_apply, smul_eq_mul]
    constructor
    · intro h p
      have hp := h p
      split_ifs at hp ⊢ <;> omega
    · intro h p
      have hp := h p
      split_ifs at hp ⊢ <;> omega
  have node_root : S.card = 672 := by
    have he : S=Nat.divisors (2^15*3^6*5^2*7) := by
      ext k
      rw [h₀,Nat.mem_divisors]
      constructor
      · rintro ⟨hk,hd⟩
        exact ⟨(node_square_divisor_reduction k hk).1 hd,by positivity⟩
      · rintro ⟨hd,hn⟩
        have hk : 0 < k := Nat.pos_of_dvd_of_pos hd (by positivity)
        exact ⟨hk,(node_square_divisor_reduction k hk).2 hd⟩
    rw [he]
    have hpcount : ∀ p e : ℕ, Nat.Prime p → (p^e).divisors.card=e+1 := by
      intro p e hp
      cases e with
      | zero => simp
      | succ e =>
        rw [Nat.card_divisors (pow_ne_zero _ hp.ne_zero),Nat.primeFactors_pow_succ]
        simp only [hp.primeFactors,Finset.prod_singleton,Nat.factorization_pow_self hp]
    rw [Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15*3^6*5^2) 7 by norm_num),Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15*3^6) (5^2) by norm_num),Nat.Coprime.card_divisors_mul (show Nat.Coprime (2^15) (3^6) by norm_num)]
    rw [hpcount 2 15 (by norm_num),hpcount 3 6 (by norm_num),hpcount 5 2 (by norm_num)]
    have h7 := hpcount 7 1 (by norm_num)
    norm_num at h7
    rw [h7]
  exact node_root

#print axioms amc12a_2003_p23
