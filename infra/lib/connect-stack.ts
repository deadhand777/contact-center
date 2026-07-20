import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as connect from 'aws-cdk-lib/aws-connect';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lex from 'aws-cdk-lib/aws-lex';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as fs from 'fs';
import * as path from 'path';

export class ConnectStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const instance = new connect.CfnInstance(this, 'Instance', {
      identityManagementType: 'CONNECT_MANAGED',
      instanceAlias: `contact-center-${this.account}`,
      attributes: { inboundCalls: true, outboundCalls: false, contactflowLogs: true },
    });

    const hours = new connect.CfnHoursOfOperation(this, 'Hours', {
      instanceArn: instance.attrArn,
      name: 'always-open',
      timeZone: 'UTC',
      config: ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY'].map((day) => ({
        day,
        startTime: { hours: 0, minutes: 0 },
        endTime: { hours: 0, minutes: 0 },
      })),
    });

    const queue = new connect.CfnQueue(this, 'EscalationQueue', {
      instanceArn: instance.attrArn,
      name: 'escalations',
      hoursOfOperationArn: hours.attrHoursOfOperationArn,
      description: 'Chats escalated by the agentic assistant',
    });

    new connect.CfnRoutingProfile(this, 'RoutingProfile', {
      instanceArn: instance.attrArn,
      name: 'escalation-handlers',
      description: 'Handles escalated chats',
      defaultOutboundQueueArn: queue.attrQueueArn,
      mediaConcurrencies: [{ channel: 'CHAT', concurrency: 2 }],
      queueConfigs: [
        { priority: 1, delay: 0, queueReference: { channel: 'CHAT', queueArn: queue.attrQueueArn } },
      ],
    });

    const bridgeFn = new lambda.Function(this, 'BridgeFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset('lambda/bridge'),
      // Lex dialog code hooks allow up to 30 s (the 8 s Connect flow-block cap
      // no longer applies — Lex invokes this Lambda, not the flow).
      timeout: cdk.Duration.seconds(25),
      description: 'Connect contact-flow bridge to the AgentCore runtime',
    });
    bridgeFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/contact-center/*`],
      }),
    );
    bridgeFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [`arn:aws:bedrock-agentcore:${this.region}:${this.account}:runtime/*`],
      }),
    );
    const lexRole = new iam.Role(this, 'LexBotRole', {
      assumedBy: new iam.ServicePrincipal('lexv2.amazonaws.com'),
    });
    lexRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['polly:SynthesizeSpeech'],
        resources: ['*'],
      }),
    );

    const bot = new lex.CfnBot(this, 'PipeBot', {
      name: 'pipe-bot',
      dataPrivacy: { ChildDirected: false },
      idleSessionTtlInSeconds: 300,
      roleArn: lexRole.roleArn,
      autoBuildBotLocales: true,
      // The pipe never does NLU (FallbackIntent catches everything), so the
      // locale list is about API availability, not language: Connect invokes
      // Lex with the contact's default locale (en_US), while our customers
      // write German — both locales must exist and be built on the alias.
      botLocales: ['de_DE', 'en_US'].map((localeId) => ({
        localeId,
        nluConfidenceThreshold: 0.4,
        intents: [
          {
            // Lex requires at least one custom intent with an utterance to build
            // the locale; this one is never meant to match real traffic.
            name: 'Noop',
            sampleUtterances: [{ utterance: 'systempingnoop' }],
            dialogCodeHook: { enabled: true },
          },
          {
            name: 'FallbackIntent',
            parentIntentSignature: 'AMAZON.FallbackIntent',
            dialogCodeHook: { enabled: true },
          },
        ],
      })),
    });

    // BotVersion is an immutable snapshot of DRAFT. The logical id carries a
    // revision suffix: bump it (V2 → V3 ...) together with the description
    // whenever the bot definition changes, so CFN cuts a fresh version and
    // repoints the alias instead of leaving it on a stale snapshot.
    const botVersion = new lex.CfnBotVersion(this, 'PipeBotVersionV3', {
      botId: bot.attrId,
      description: 'v3 - en_US locale for the Connect default',
      botVersionLocaleSpecification: ['de_DE', 'en_US'].map((localeId) => ({
        localeId,
        botVersionLocaleDetails: { sourceBotVersion: 'DRAFT' },
      })),
    });

    const botAlias = new lex.CfnBotAlias(this, 'PipeBotAlias', {
      botId: bot.attrId,
      botAliasName: 'live',
      botVersion: botVersion.attrBotVersion,
      botAliasLocaleSettings: ['de_DE', 'en_US'].map((localeId) => ({
        localeId,
        botAliasLocaleSetting: {
          enabled: true,
          codeHookSpecification: {
            lambdaCodeHook: {
              lambdaArn: bridgeFn.functionArn,
              codeHookInterfaceVersion: '1.0',
            },
          },
        },
      })),
    });

    bridgeFn.addPermission('LexInvoke', {
      principal: new iam.ServicePrincipal('lexv2.amazonaws.com'),
      sourceArn: botAlias.attrArn,
    });

    new connect.CfnIntegrationAssociation(this, 'BridgeAssociation', {
      instanceId: instance.attrArn,
      integrationType: 'LEX_BOT',
      integrationArn: botAlias.attrArn,
    });

    const flowTemplate = fs.readFileSync(path.join(__dirname, 'flows', 'inbound-chat.json'), 'utf8');
    const flow = new connect.CfnContactFlow(this, 'InboundChatFlow', {
      instanceArn: instance.attrArn,
      name: 'inbound-chat',
      type: 'CONTACT_FLOW',
      content: cdk.Fn.sub(
        flowTemplate.replace(/%%LEX_ALIAS_ARN%%/g, '${LexAliasArn}').replace(/%%QUEUE_ARN%%/g, '${QueueArn}'),
        { LexAliasArn: botAlias.attrArn, QueueArn: queue.attrQueueArn },
      ),
    });

    const params: Record<string, string> = {
      '/contact-center/connect-instance-id': instance.attrId,
      '/contact-center/contact-flow-id': flow.attrContactFlowArn,
      '/contact-center/escalation-queue-id': queue.attrQueueArn,
      '/contact-center/lex-alias-arn': botAlias.attrArn,
    };
    Object.entries(params).forEach(([name, value], i) => {
      new ssm.StringParameter(this, `ConnectParam${i}`, { parameterName: name, stringValue: value });
    });
  }
}
