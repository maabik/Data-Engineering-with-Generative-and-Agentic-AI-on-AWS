package main

# Any IAM policy that grants bedrock:InvokeModel must coexist with at
# least one aws_bedrock_guardrail_version reference in the same plan.
# The intent is "you can't ship the ability to call a model without
# also shipping a guardrail."

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_iam_policy"
  rc.change.actions[_] == "create"
  policy := json.unmarshal(rc.change.after.policy)
  stmt := policy.Statement[_]
  stmt.Effect == "Allow"
  has_bedrock_invoke(stmt.Action)
  not plan_has_guardrail
  msg := sprintf("IAM policy %q grants bedrock:InvokeModel but plan has no Bedrock guardrail", [rc.address])
}

has_bedrock_invoke(actions) {
  actions == "bedrock:InvokeModel"
}
has_bedrock_invoke(actions) {
  actions[_] == "bedrock:InvokeModel"
}
has_bedrock_invoke(actions) {
  actions[_] == "bedrock:*"
}

plan_has_guardrail {
  rc := input.resource_changes[_]
  rc.type == "aws_bedrock_guardrail"
}
