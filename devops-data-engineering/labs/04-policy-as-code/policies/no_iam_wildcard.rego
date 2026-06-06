package main

# Block IAM policies with Action="*" or Resource="*" outside of an
# approved set of break-glass roles. The approved list is a hardcoded
# allowlist; PRs that need to add to it require review by @your-org/security.

approved_wildcard_roles := {
  "break-glass-admin",
  "incident-response",
}

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_iam_policy"
  rc.change.actions[_] == "create"
  policy := json.unmarshal(rc.change.after.policy)
  stmt := policy.Statement[_]
  stmt.Effect == "Allow"
  has_wildcard(stmt.Action)
  not is_approved(rc.address)
  msg := sprintf("IAM policy %q grants wildcard Action; not on approved list", [rc.address])
}

has_wildcard(actions) {
  actions == "*"
}
has_wildcard(actions) {
  actions[_] == "*"
}

is_approved(addr) {
  some r
  approved_wildcard_roles[r]
  contains(addr, r)
}
