import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { KnowledgeStack } from '../lib/knowledge-stack';

describe('KnowledgeStack', () => {
  const app = new cdk.App();
  const stack = new KnowledgeStack(app, 'TestStack', {
    env: { account: '111111111111', region: 'eu-central-1' },
  });
  const template = Template.fromStack(stack);

  test('knowledge base uses S3 Vectors storage', () => {
    template.hasResourceProperties('AWS::Bedrock::KnowledgeBase', {
      StorageConfiguration: { Type: 'S3_VECTORS' },
    });
  });

  test('vector index matches titan v2 embeddings', () => {
    template.hasResourceProperties('AWS::S3Vectors::Index', {
      Dimension: 1024,
      DistanceMetric: 'cosine',
      DataType: 'float32',
    });
  });

  test('guardrail exists with PII anonymization', () => {
    template.resourceCountIs('AWS::Bedrock::Guardrail', 1);
    template.hasResourceProperties('AWS::Bedrock::Guardrail', {
      SensitiveInformationPolicyConfig: {
        PiiEntitiesConfig: Match.arrayWith([
          Match.objectLike({ Type: 'EMAIL', Action: 'ANONYMIZE' }),
          Match.objectLike({ Type: 'PHONE', Action: 'ANONYMIZE' }),
          Match.objectLike({ Type: 'NAME', Action: 'ANONYMIZE' }),
        ]),
      },
    });
  });

  test('blocked messaging does not promise a handoff', () => {
    const guardrail = Object.values(template.findResources('AWS::Bedrock::Guardrail'))[0];
    const { BlockedInputMessaging, BlockedOutputsMessaging } = guardrail.Properties;
    for (const message of [BlockedInputMessaging, BlockedOutputsMessaging]) {
      // The supervisor returns a blocked turn with escalate=false, so the text
      // must not announce a transfer that never happens.
      expect(message).toContain('Compliance-Gr\u00fcnden');
      expect(message).not.toMatch(/verbinde Sie|Mitarbeiter/);
    }
  });

  test('ssm parameters are published for the handoff', () => {
    for (const name of [
      '/contact-center/kb-id',
      '/contact-center/guardrail-id',
      '/contact-center/guardrail-version',
      '/contact-center/data-source-id',
      '/contact-center/agent-policy-arn',
    ]) {
      template.hasResourceProperties('AWS::SSM::Parameter', { Name: name });
    }
  });

  test('docs bucket blocks public access', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  test('balance lambda exists with python runtime and handler', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'python3.13',
      Handler: 'handler.handler',
    });
  });

  test('balance lambda arn is published to ssm', () => {
    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/contact-center/balance-fn-arn',
    });
  });
});
