plugin "aws" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# Rules tuned for data eng:
rule "terraform_naming_convention" { enabled = true }
rule "terraform_unused_declarations" { enabled = true }
rule "terraform_required_version"   { enabled = true }
rule "terraform_required_providers" { enabled = true }
