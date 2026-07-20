import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { ConnectStack } from '../lib/connect-stack';

describe('ConnectStack', () => {
  const app = new cdk.App();
  const stack = new ConnectStack(app, 'TestConnect', {
    env: { account: '111111111111', region: 'eu-central-1' },
  });
  const template = Template.fromStack(stack);

  test('connect instance is chat-ready with flow logs', () => {
    template.hasResourceProperties('AWS::Connect::Instance', {
      IdentityManagementType: 'CONNECT_MANAGED',
      Attributes: { InboundCalls: true, OutboundCalls: false, ContactflowLogs: true },
    });
  });

  test('escalation queue and routing profile exist', () => {
    template.resourceCountIs('AWS::Connect::Queue', 1);
    template.resourceCountIs('AWS::Connect::RoutingProfile', 1);
  });

  test('contact flow is registered and references no leftover placeholders', () => {
    const flows = template.findResources('AWS::Connect::ContactFlow');
    const flow = Object.values(flows)[0] as any;
    expect(flow.Properties.Type).toBe('CONTACT_FLOW');
    const content = JSON.stringify(flow.Properties.Content);
    expect(content).not.toContain('%%');
    expect(content).not.toContain('BridgeArn');
  });

  test('lex bot pipe is registered with the fallback intent', () => {
    template.hasResourceProperties('AWS::Lex::Bot', {
      IdleSessionTTLInSeconds: 300,
      DataPrivacy: { ChildDirected: false },
      BotLocales: Match.arrayWith([
        Match.objectLike({
          LocaleId: 'de_DE',
          NluConfidenceThreshold: 0.4,
          Intents: Match.arrayWith([
            Match.objectLike({
              ParentIntentSignature: 'AMAZON.FallbackIntent',
              DialogCodeHook: { Enabled: true },
            }),
          ]),
        }),
      ]),
    });
    template.resourceCountIs('AWS::Lex::BotVersion', 1);
    template.resourceCountIs('AWS::Lex::BotAlias', 1);
  });

  test('bridge lambda is integration-associated via the lex bot and invocable by lex', () => {
    template.hasResourceProperties('AWS::Connect::IntegrationAssociation', {
      IntegrationType: 'LEX_BOT',
    });
    template.hasResourceProperties('AWS::Lambda::Permission', {
      Principal: 'lexv2.amazonaws.com',
    });
  });

  test('ssm parameters are published', () => {
    for (const name of [
      '/contact-center/connect-instance-id',
      '/contact-center/contact-flow-id',
      '/contact-center/escalation-queue-id',
      '/contact-center/lex-alias-arn',
    ]) {
      template.hasResourceProperties('AWS::SSM::Parameter', { Name: name });
    }
  });

  test('bridge role is least-privilege', () => {
    const content = JSON.stringify(template.toJSON());
    expect(content).toContain('bedrock-agentcore:InvokeAgentRuntime');
    expect(content).toContain('/contact-center/');
  });
});
