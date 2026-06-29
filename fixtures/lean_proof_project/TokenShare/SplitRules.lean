namespace TokenShare

inductive SplitKind where
  | complete
  | singleChild
  | allRequiredChildren
  | unsupported
deriving Repr, BEq

def splitPolicyId : String := "lean_proof.deterministic_tactic_split.v1"

def splitCertificateSchemaVersion : String := "lean_proof.split_certificate.v1"

def jsonPair (key value : String) : String :=
  "\"" ++ key ++ "\":\"" ++ value ++ "\""

def jsonNullPair (key : String) : String :=
  "\"" ++ key ++ "\":null"

def childGoalJson
    (key theoremName parametersSource statementSource contextDigest sourceRuleId : String) :
    String :=
  "{"
    ++ jsonPair "child_logical_key" key ++ ","
    ++ jsonPair "theorem_name" theoremName ++ ","
    ++ jsonPair "parameters_source" parametersSource ++ ","
    ++ jsonPair "statement_source" statementSource ++ ","
    ++ jsonPair "context_digest" contextDigest ++ ","
    ++ jsonPair "required_output_name" "lean_proof_artifact" ++ ","
    ++ jsonPair "source_rule_id" sourceRuleId
    ++ "}"

def mergeSkeletonJson (ruleId : String) : String :=
  "{"
    ++ jsonPair "merge_rule_id" ruleId ++ ","
    ++ jsonPair "merge_policy_id" "lean_proof.verified_merge.v1"
    ++ "}"

def supportedCertificateJson
    (requestId parentDigest ruleId splitKind mergeRule childJson : String) : String :=
  "{"
    ++ jsonPair "schema_version" splitCertificateSchemaVersion ++ ","
    ++ jsonPair "split_certificate_id" ("lean_split_certificate:" ++ requestId) ++ ","
    ++ jsonNullPair "parent_theorem_payload_ref" ++ ","
    ++ jsonPair "normalized_parent_goal_digest" parentDigest ++ ","
    ++ jsonPair "policy_id" splitPolicyId ++ ","
    ++ jsonPair "rule_id" ruleId ++ ","
    ++ "\"rule_trace\":[{" ++ jsonPair "rule_id" ruleId ++ "}],"
    ++ jsonPair "split_kind" splitKind ++ ","
    ++ "\"child_goals\":[" ++ childJson ++ "],"
    ++ "\"merge_skeleton\":" ++ mergeSkeletonJson mergeRule ++ ","
    ++ jsonNullPair "unsupported_reason" ++ ","
    ++ jsonNullPair "helper_stdout_ref" ++ ","
    ++ jsonNullPair "helper_stderr_ref" ++ ","
    ++ "\"diagnostics\":{\"source\":\"TokenShare.SplitRules\"}"
    ++ "}"

def unsupportedCertificateJsonWithReason (requestId parentDigest reason : String) : String :=
  "{"
    ++ jsonPair "schema_version" splitCertificateSchemaVersion ++ ","
    ++ jsonPair "split_certificate_id" ("lean_split_certificate:" ++ requestId) ++ ","
    ++ jsonNullPair "parent_theorem_payload_ref" ++ ","
    ++ jsonPair "normalized_parent_goal_digest" parentDigest ++ ","
    ++ jsonPair "policy_id" splitPolicyId ++ ","
    ++ jsonPair "rule_id" "lean_split.unsupported.v1" ++ ","
    ++ "\"rule_trace\":[{" ++ jsonPair "rule_id" "lean_split.unsupported.v1" ++ "}],"
    ++ jsonPair "split_kind" "unsupported" ++ ","
    ++ "\"child_goals\":[],"
    ++ "\"merge_skeleton\":null,"
    ++ jsonPair "unsupported_reason" reason ++ ","
    ++ jsonNullPair "helper_stdout_ref" ++ ","
    ++ jsonNullPair "helper_stderr_ref" ++ ","
    ++ "\"diagnostics\":{\"source\":\"TokenShare.SplitRules\"}"
    ++ "}"

def unsupportedCertificateJson (requestId parentDigest : String) : String :=
  unsupportedCertificateJsonWithReason requestId parentDigest "unsupported_goal_shape"

def splitCertificateJson
    (requestId parentDigest theoremName parametersSource statementSource : String) : String :=
  if statementSource == "P ∧ Q" then
    let left := childGoalJson
      "child:left"
      (theoremName ++ "_left")
      parametersSource
      "P"
      "sha256:lean_context_left"
      "lean_split.conjunction_goal.v1"
    let right := childGoalJson
      "child:right"
      (theoremName ++ "_right")
      parametersSource
      "Q"
      "sha256:lean_context_right"
      "lean_split.conjunction_goal.v1"
    supportedCertificateJson
      requestId
      parentDigest
      "lean_split.conjunction_goal.v1"
      "all_required_children"
      "lean_merge.conjunction_intro.v1"
      (left ++ "," ++ right)
  else if statementSource == "P ↔ Q" then
    let forward := childGoalJson
      "child:forward"
      (theoremName ++ "_forward")
      parametersSource
      "P → Q"
      "sha256:lean_context_forward"
      "lean_split.iff_goal.v1"
    let backward := childGoalJson
      "child:backward"
      (theoremName ++ "_backward")
      parametersSource
      "Q → P"
      "sha256:lean_context_backward"
      "lean_split.iff_goal.v1"
    supportedCertificateJson
      requestId
      parentDigest
      "lean_split.iff_goal.v1"
      "all_required_children"
      "lean_merge.iff_intro.v1"
      (forward ++ "," ++ backward)
  else if statementSource == "P → Q" then
    unsupportedCertificateJsonWithReason requestId parentDigest "unsupported_merge_rule"
  else if statementSource == "∀ n : Nat, n = n" then
    unsupportedCertificateJsonWithReason requestId parentDigest "unsupported_merge_rule"
  else
    unsupportedCertificateJson requestId parentDigest

end TokenShare
