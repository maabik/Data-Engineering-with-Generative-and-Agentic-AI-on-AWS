import * as cdk from "aws-cdk-lib";
import * as glue from "aws-cdk-lib/aws-glue";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";
import * as path from "path";

export interface GluePipelineStackProps extends cdk.StackProps {
  namePrefix: string;
  glueWorkers: number;
  rawRetentionDays: number;
  tags: Record<string, string>;
}

export class GluePipelineStack extends cdk.Stack {
  public readonly ingestJob: glue.CfnJob;
  public readonly curateJob: glue.CfnJob;
  public readonly dqJob: glue.CfnJob;
  public readonly curatedDb: glue.CfnDatabase;

  constructor(scope: Construct, id: string, props: GluePipelineStackProps) {
    super(scope, id, props);
    Object.entries(props.tags).forEach(([k, v]) => cdk.Tags.of(this).add(k, v));

    const raw = new s3.Bucket(this, "raw", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(props.rawRetentionDays) }],
    });
    const curated = new s3.Bucket(this, "curated", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
    });
    const scripts = new s3.Bucket(this, "scripts", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
    });

    new s3deploy.BucketDeployment(this, "scripts-deploy", {
      destinationBucket: scripts,
      sources: [s3deploy.Source.asset(path.join(__dirname, "..", "..", "glue_jobs"))],
    });

    new glue.CfnDatabase(this, "raw-db", {
      catalogId: this.account,
      databaseInput: { name: `${props.namePrefix}_raw` },
    });
    this.curatedDb = new glue.CfnDatabase(this, "curated-db", {
      catalogId: this.account,
      databaseInput: { name: `${props.namePrefix}_curated` },
    });

    const role = new iam.Role(this, "glue-role", {
      assumedBy: new iam.ServicePrincipal("glue.amazonaws.com"),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSGlueServiceRole")],
    });
    raw.grantReadWrite(role);
    curated.grantReadWrite(role);
    scripts.grantRead(role);

    const mkJob = (id: string, scriptKey: string, defaults: Record<string, string>) =>
      new glue.CfnJob(this, id, {
        name: `${props.namePrefix}-${id}`,
        role: role.roleArn,
        glueVersion: "4.0",
        numberOfWorkers: props.glueWorkers,
        workerType: "G.1X",
        command: {
          name: "glueetl",
          scriptLocation: `s3://${scripts.bucketName}/${scriptKey}`,
          pythonVersion: "3",
        },
        defaultArguments: defaults,
      });

    this.ingestJob = mkJob("ingest-orders", "ingest_orders.py", {
      "--raw_bucket": raw.bucketName,
    });
    this.curateJob = mkJob("curate-orders", "curate_orders.py", {
      "--raw_bucket": raw.bucketName,
      "--curated_bucket": curated.bucketName,
      "--curated_db": this.curatedDb.ref,
      "--datalake-formats": "iceberg",
    });
    this.dqJob = mkJob("dq-orders", "dq_orders.py", {
      "--curated_bucket": curated.bucketName,
      "--curated_db": this.curatedDb.ref,
    });
  }
}
