# TLS Termination at an AWS Load Balancer

Deployable CloudFormation implementation of the book excerpt's "encrypt
data-in-transit" steps: Route 53 → ACM → ALB with HTTPS listener.

## Files

- `guide.md` — parameter-by-parameter walkthrough. **Read this first.**
- `tls-alb.yaml` — CloudFormation template.

## What gets deployed

- ACM public certificate (DNS-validated via Route 53)
- Security group allowing 443 (and 80 for redirect) from the internet
- Application Load Balancer (internet-facing, across your subnets)
- HTTPS listener on 443 using the ACM cert, TLS 1.2+1.3 policy
- HTTP listener on 80 that 301-redirects to HTTPS
- Empty target group (register your EC2/IP backends after deploy)
- Route 53 alias A record pointing your domain at the ALB

## Prerequisites

- AWS CLI configured with credentials that can create ACM, ELBv2, EC2-SG,
  and Route 53 resources.
- A Route 53 **public** hosted zone authoritative for your domain.
- A VPC with at least two **public** subnets in different AZs.

## Deploy

```bash
aws cloudformation deploy \
  --stack-name tls-alb \
  --template-file tls-alb.yaml \
  --parameter-overrides \
      DomainName=api.example.com \
      HostedZoneId=Z0123456789ABCDEFGHIJ \
      VpcId=vpc-0123456789abcdef0 \
      SubnetIds=subnet-aaaa,subnet-bbbb \
      TargetPort=80 \
      HealthCheckPath=/ \
  --capabilities CAPABILITY_NAMED_IAM
```

Stack creation blocks on ACM DNS validation (~2–5 min once the CNAME is
written to Route 53 — the template writes it for you).

## Verify

```bash
curl -vI https://api.example.com/
```

You should see a valid TLS handshake and a `503 Service Unavailable` from
the ALB (expected — no targets registered yet).

## Register targets

Grab the target group ARN from stack outputs and register backends:

```bash
aws elbv2 register-targets \
  --target-group-arn <TargetGroupArn-from-outputs> \
  --targets Id=i-0123456789abcdef0
```

## Tear down

```bash
aws cloudformation delete-stack --stack-name tls-alb
```

## Using this with AI coding tools

`guide.md` is structured so an AI coding assistant can walk you through each
parameter interactively. Open the folder in your tool of choice and try one
of the prompts below.

### Claude Code ([claude.com/claude-code](https://claude.com/claude-code))

```text
Read guide.md and tls-alb.yaml. Walk me through each parameter one at a
time — explain the trade-offs, ask me for my value, then show me where it
lands in the template. Stop after each parameter for confirmation.
```

For deployment, ask Claude Code to run the `aws cloudformation deploy`
command directly — it will propose the command and wait for your approval
before executing.

### Kiro ([kiro.dev](https://kiro.dev))

Kiro's spec-driven workflow fits this well. Point Kiro at `guide.md` as the
requirements input and ask it to generate a deployment task list, then step
through each task. The existing `tls-alb.yaml` can serve as the reference
implementation Kiro validates its output against.

### OpenAI Codex / ChatGPT

Paste `guide.md` plus `tls-alb.yaml` into a Codex session and ask:

```text
Act as a deployment coach. For each parameter in guide.md, ask me one
question, wait for my answer, then fill it into a deploy command I can
copy-paste. At the end, hand me the final command.
```

### Cursor / GitHub Copilot Chat

Open the folder in Cursor (or VS Code with Copilot Chat) and use:

```text
@workspace walk me through guide.md parameter by parameter and show me
exactly which lines in tls-alb.yaml each one maps to.
```

### Tips for any AI tool

- Feed it **both** `guide.md` (the narrative) and `tls-alb.yaml` (the
  ground truth). Without the template, the model will hallucinate field
  names.
- If the tool suggests changes to the template, run `aws cloudformation
  validate-template --template-body file://tls-alb.yaml` before deploying.
- Review any IAM or security-group edits the AI proposes — these are the
  most common places for overly-permissive suggestions.
