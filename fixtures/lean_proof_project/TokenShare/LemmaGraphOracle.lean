import TokenShare.LemmaGraphCases

namespace TokenShare.LemmaGraphOracle

theorem medium_logic_leaf_p
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : P := by
  exact TokenShare.LemmaGraphCases.medium_logic_leaf_p P Q R hP hpq hqr

theorem medium_logic_leaf_p_to_q
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : P -> Q := by
  exact TokenShare.LemmaGraphCases.medium_logic_leaf_p_to_q P Q R hP hpq hqr

theorem medium_logic_intermediate_q
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : Q := by
  exact TokenShare.LemmaGraphCases.medium_logic_intermediate_q P Q R hP hpq hqr

theorem medium_logic_leaf_q_to_r
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : Q -> R := by
  exact TokenShare.LemmaGraphCases.medium_logic_leaf_q_to_r P Q R hP hpq hqr

theorem medium_logic_root_r
    (P Q R : Prop) (hP : P) (hpq : P -> Q) (hqr : Q -> R) : R := by
  exact TokenShare.LemmaGraphCases.medium_logic_root_r P Q R hP hpq hqr

theorem function_set_leaf_d_subset_e
    (α β : Type)
    (D E F : α -> TokenShare.LemmaGraphCases.LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, D x y -> E x y := by
  exact TokenShare.LemmaGraphCases.function_set_leaf_d_subset_e
    α β D E F hDE hEF

theorem function_set_leaf_e_subset_f
    (α β : Type)
    (D E F : α -> TokenShare.LemmaGraphCases.LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, E x y -> F x y := by
  exact TokenShare.LemmaGraphCases.function_set_leaf_e_subset_f
    α β D E F hDE hEF

theorem function_set_intermediate_d_subset_f
    (α β : Type)
    (D E F : α -> TokenShare.LemmaGraphCases.LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) : ∀ x y, D x y -> F x y := by
  exact TokenShare.LemmaGraphCases.function_set_intermediate_d_subset_f
    α β D E F hDE hEF

theorem function_set_root_d_subset_f
    (α β : Type)
    (D E F : α -> TokenShare.LemmaGraphCases.LocalSet β)
    (hDE : ∀ x y, D x y -> E x y)
    (hEF : ∀ x y, E x y -> F x y) :
    ∀ x, TokenShare.LemmaGraphCases.LocalSubset (D x) (F x) := by
  exact TokenShare.LemmaGraphCases.function_set_root_d_subset_f
    α β D E F hDE hEF

theorem induction_leaf_all_p
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, P n := by
  exact TokenShare.LemmaGraphCases.induction_leaf_all_p P Q R h0 hstep hpq hqr

theorem induction_leaf_p_to_q
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, P n -> Q n := by
  exact TokenShare.LemmaGraphCases.induction_leaf_p_to_q P Q R h0 hstep hpq hqr

theorem induction_intermediate_all_q
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, Q n := by
  exact TokenShare.LemmaGraphCases.induction_intermediate_all_q P Q R h0 hstep hpq hqr

theorem induction_leaf_q_to_r
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, Q n -> R n := by
  exact TokenShare.LemmaGraphCases.induction_leaf_q_to_r P Q R h0 hstep hpq hqr

theorem induction_root_all_r
    (P Q R : Nat -> Prop)
    (h0 : P 0)
    (hstep : ∀ n, P n -> P (Nat.succ n))
    (hpq : ∀ n, P n -> Q n)
    (hqr : ∀ n, Q n -> R n) : ∀ n, R n := by
  exact TokenShare.LemmaGraphCases.induction_root_all_r P Q R h0 hstep hpq hqr

end TokenShare.LemmaGraphOracle
