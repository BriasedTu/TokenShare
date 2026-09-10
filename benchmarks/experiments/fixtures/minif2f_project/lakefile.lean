import Lake
open Lake DSL

package «miniF2F-lean4» {}

@[default_target]
lean_lib TokenShare where
  roots := #[`TokenShare.Preamble]

require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v4.24.0"
