namespace TokenShare.LemmaGraphCases

abbrev LocalSet (β : Type) := β -> Prop

abbrev LocalSubset (A B : LocalSet β) : Prop := ∀ y, A y -> B y

theorem medium_logic_leaf_p
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : P := by
  exact hP

theorem medium_logic_leaf_p_to_q
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : P -> Q := by
  exact hpq

theorem medium_logic_intermediate_q
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : Q := by
  exact medium_logic_leaf_p_to_q P Q R hP hpq hqr
    (medium_logic_leaf_p P Q R hP hpq hqr)

theorem medium_logic_leaf_q_to_r
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : Q -> R := by
  exact hqr

theorem medium_logic_root_r
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : R := by
  exact medium_logic_leaf_q_to_r P Q R hP hpq hqr
    (medium_logic_intermediate_q P Q R hP hpq hqr)

theorem function_set_leaf_d_subset_e
    (α β : Type)
    (D E F : α -> LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, D x y -> E x y := by
  exact hDE

theorem function_set_leaf_e_subset_f
    (α β : Type)
    (D E F : α -> LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, E x y -> F x y := by
  exact hEF

theorem function_set_intermediate_d_subset_f
    (α β : Type)
    (D E F : α -> LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, D x y -> F x y := by
  intro x y hDy
  exact function_set_leaf_e_subset_f α β D E F hDE hEF x y
    (function_set_leaf_d_subset_e α β D E F hDE hEF x y hDy)

theorem function_set_root_d_subset_f
    (α β : Type)
    (D E F : α -> LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) :
    ∀ x, LocalSubset (D x) (F x) := by
  exact function_set_intermediate_d_subset_f α β D E F hDE hEF

theorem induction_leaf_all_p
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, P n := by
  intro n
  induction n with
  | zero =>
      exact h0
  | succ n ih =>
      exact hstep n ih

theorem induction_leaf_p_to_q
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, P n -> Q n := by
  exact hpq

theorem induction_intermediate_all_q
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, Q n := by
  intro n
  exact induction_leaf_p_to_q P Q R h0 hstep hpq hqr n
    (induction_leaf_all_p P Q R h0 hstep hpq hqr n)

theorem induction_leaf_q_to_r
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, Q n -> R n := by
  exact hqr

theorem induction_root_all_r
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, R n := by
  intro n
  exact induction_leaf_q_to_r P Q R h0 hstep hpq hqr n
    (induction_intermediate_all_q P Q R h0 hstep hpq hqr n)

end TokenShare.LemmaGraphCases
