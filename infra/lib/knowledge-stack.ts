import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ssm from 'aws-cdk-lib/aws-ssm';

const EMBEDDING_MODEL = 'amazon.titan-embed-text-v2:0';
const MODEL_PROFILE = 'eu.amazon.nova-2-lite-v1:0';

export class KnowledgeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const docsBucket = new s3.Bucket(this, 'DocsBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    new s3deploy.BucketDeployment(this, 'CorpusDeployment', {
      sources: [s3deploy.Source.asset('../docs/corpus')],
      destinationBucket: docsBucket,
    });

    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'VectorBucket', {
      vectorBucketName: `contact-center-vectors-${this.account}`,
    });

    const vectorIndex = new s3vectors.CfnIndex(this, 'VectorIndex', {
      vectorBucketName: vectorBucket.vectorBucketName!,
      indexName: 'contact-center-kb-index',
      dataType: 'float32',
      dimension: 1024,
      distanceMetric: 'cosine',
      metadataConfiguration: {
        // Bedrock KB stores the chunk text under this key; it must be non-filterable.
        nonFilterableMetadataKeys: ['AMAZON_BEDROCK_TEXT'],
      },
    });
    vectorIndex.addResourceDependency(vectorBucket);

    const kbRole = new iam.Role(this, 'KnowledgeBaseRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
    });
    docsBucket.grantRead(kbRole);
    kbRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/${EMBEDDING_MODEL}`,
        ],
      }),
    );
    kbRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          's3vectors:GetIndex',
          's3vectors:PutVectors',
          's3vectors:GetVectors',
          's3vectors:QueryVectors',
          's3vectors:DeleteVectors',
          's3vectors:ListVectors',
        ],
        resources: [vectorIndex.attrIndexArn],
      }),
    );

    const kb = new bedrock.CfnKnowledgeBase(this, 'KnowledgeBase', {
      name: 'contact-center-kb',
      roleArn: kbRole.roleArn,
      knowledgeBaseConfiguration: {
        type: 'VECTOR',
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: `arn:aws:bedrock:${this.region}::foundation-model/${EMBEDDING_MODEL}`,
          embeddingModelConfiguration: {
            bedrockEmbeddingModelConfiguration: {
              dimensions: 1024,
              embeddingDataType: 'FLOAT32',
            },
          },
        },
      },
      storageConfiguration: {
        type: 'S3_VECTORS',
        s3VectorsConfiguration: { indexArn: vectorIndex.attrIndexArn },
      },
    });
    kb.node.addDependency(vectorIndex);
    kb.node.addDependency(kbRole);

    const dataSource = new bedrock.CfnDataSource(this, 'CorpusSource', {
      name: 'corpus',
      knowledgeBaseId: kb.attrKnowledgeBaseId,
      dataSourceConfiguration: {
        type: 'S3',
        s3Configuration: { bucketArn: docsBucket.bucketArn },
      },
      dataDeletionPolicy: 'RETAIN',
    });

    const guardrail = new bedrock.CfnGuardrail(this, 'Guardrail', {
      name: 'contact-center-guardrail',
      blockedInputMessaging:
        'Diese Anfrage kann ich aus Compliance-Gründen nicht bearbeiten. Ich verbinde Sie gerne mit einem Mitarbeiter.',
      blockedOutputsMessaging:
        'Diese Antwort kann ich aus Compliance-Gründen nicht geben. Ich verbinde Sie gerne mit einem Mitarbeiter.',
      sensitiveInformationPolicyConfig: {
        piiEntitiesConfig: [
          { type: 'EMAIL', action: 'ANONYMIZE' },
          { type: 'PHONE', action: 'ANONYMIZE' },
          { type: 'NAME', action: 'ANONYMIZE' },
        ],
      },
      topicPolicyConfig: {
        topicsConfig: [
          {
            name: 'investment-advice',
            type: 'DENY',
            definition:
              'Personalized investment advice or recommendations to buy, sell, or hold securities or crypto assets.',
            examples: ['Should I buy Tesla stock?', 'Soll ich in Bitcoin investieren?'],
          },
        ],
      },
    });

    const balanceFn = new lambda.Function(this, 'BalanceFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset('lambda/balance'),
      timeout: cdk.Duration.seconds(10),
      description: 'Mock core-banking balance lookup (synthetic data only)',
    });

    const agentPolicy = new iam.ManagedPolicy(this, 'AgentPolicy', {
      managedPolicyName: 'contact-center-agent-policy',
      statements: [
        new iam.PolicyStatement({
          actions: ['bedrock:Retrieve'],
          resources: [kb.attrKnowledgeBaseArn],
        }),
        new iam.PolicyStatement({
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: [
            `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${MODEL_PROFILE}`,
            `arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0`,
          ],
        }),
        new iam.PolicyStatement({
          actions: ['bedrock:ApplyGuardrail'],
          resources: [guardrail.attrGuardrailArn],
        }),
        new iam.PolicyStatement({
          actions: ['ssm:GetParameter'],
          resources: [
            `arn:aws:ssm:${this.region}:${this.account}:parameter/contact-center/*`,
          ],
        }),
      ],
    });

    const params: Record<string, string> = {
      '/contact-center/kb-id': kb.attrKnowledgeBaseId,
      '/contact-center/guardrail-id': guardrail.attrGuardrailId,
      '/contact-center/guardrail-version': 'DRAFT',
      '/contact-center/data-source-id': dataSource.attrDataSourceId,
      '/contact-center/agent-policy-arn': agentPolicy.managedPolicyArn,
      '/contact-center/balance-fn-arn': balanceFn.functionArn,
    };
    Object.entries(params).forEach(([name, value], i) => {
      new ssm.StringParameter(this, `Param${i}`, {
        parameterName: name,
        stringValue: value,
      });
    });
  }
}
