# CDK comparison

Same pipeline as [`../terraform/`](../terraform/), expressed in TypeScript
CDK. **This is for shape comparison, not the recommended deploy path.**
The labs all reference Terraform paths.

See [`../../docs/iac-tool-tradeoffs.md`](../../docs/iac-tool-tradeoffs.md)
for the rationale.

## Layout

```
cdk/
├── bin/app.ts              # entrypoint; reads --context env=sandbox|prod
├── stacks/
│   ├── glue-pipeline-stack.ts
│   ├── orchestration-stack.ts
│   └── observability-stack.ts
├── cdk.json
├── package.json
└── tsconfig.json
```

## Deploy

```bash
npm install
npx cdk bootstrap                              # one-time per account/region
npx cdk diff   --context env=sandbox --context owner=$USER
npx cdk deploy --context env=sandbox --context owner=$USER --all
```

## Notable differences from the Terraform version

- IAM policies are ergonomic — `bucket.grantReadWrite(role)` collapses
  several lines of HCL.
- Stateful resources (S3 buckets, Glue databases) get
  `RemovalPolicy.RETAIN` and explicit `applyRemovalPolicy` calls; the
  Terraform path relies on `prevent_destroy` lifecycle hooks instead.
- Drift detection is via CloudFormation, which is weaker on Glue + Lake
  Formation than `terraform plan`.

The README in [`../`](..) explains why the chapter codebase recommends the
Terraform path.
