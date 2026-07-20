import * as cdk from 'aws-cdk-lib';
import { KnowledgeStack } from '../lib/knowledge-stack';
import { ConnectStack } from '../lib/connect-stack';

const app = new cdk.App();
new KnowledgeStack(app, 'ContactCenterKnowledge', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'eu-central-1',
  },
});

new ConnectStack(app, 'ContactCenterConnect', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'eu-central-1',
  },
});
