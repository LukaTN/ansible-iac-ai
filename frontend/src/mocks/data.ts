import type {
  AuthConfig,
  AuthProfile,
  AuthUser,
  ChatMessage,
  DocsStatus,
  RagStatus,
  RollbackVersion,
  ScrapeSession,
  StatsPayload,
  Thread,
} from '@/lib/types';
import type { GenerationStep, ThoughtEntry } from '@/lib/socket';

export const MOCK_ADMIN_ID = 1;
export const MOCK_MEMBER_ID = 2;
export const MOCK_TEMP_ID = 3;

export const MOCK_THREAD = {
  s3: 101,
  k8s: 102,
  nginx: 103,
  postgres: 104,
  deploy: 105,
  azure: 106,
} as const;

function iso(hoursAgo: number): string {
  return new Date(Date.now() - hoursAgo * 3600_000).toISOString();
}

export const mockAdmin: AuthUser = {
  id: MOCK_ADMIN_ID,
  email: 'designer.admin@example.com',
  display_name: 'Ada Designer',
  role: 'admin',
  is_active: true,
  provider: 'local',
  has_password: true,
  can_change_password: true,
  must_change_password: false,
  created_at: iso(24 * 40),
  last_login_at: iso(0.2),
};

export const mockMember: AuthUser = {
  id: MOCK_MEMBER_ID,
  email: 'designer@example.com',
  display_name: 'Sam Operator',
  role: 'user',
  is_active: true,
  provider: 'local',
  has_password: true,
  can_change_password: true,
  must_change_password: false,
  created_at: iso(24 * 12),
  last_login_at: iso(1.5),
};

export const mockTempUser: AuthUser = {
  id: MOCK_TEMP_ID,
  email: 'temp.designer@example.com',
  display_name: 'New Hire',
  role: 'user',
  is_active: true,
  provider: 'keycloak',
  has_password: true,
  can_change_password: true,
  must_change_password: true,
  created_at: iso(0.1),
  last_login_at: iso(0.05),
};

export const mockAuthConfig: AuthConfig = {
  auth_mode: 'local',
  oidc_enabled: false,
  local_login_enabled: true,
  registration_enabled: true,
  app_admin_ui: true,
  oidc_login_url: null,
};

export const mockAuthConfigInviteOnly: AuthConfig = {
  ...mockAuthConfig,
  registration_enabled: false,
};

export function mockProfileFor(user: AuthUser, threadCount: number): AuthProfile {
  const capped = user.role === 'user';
  return {
    user,
    usage: capped
      ? {
          token_budget_limit: 250_000,
          token_budget_used: 86_400,
          token_budget_remaining: 163_600,
          unlimited: false,
        }
      : {
          token_budget_limit: 0,
          token_budget_used: 412_880,
          token_budget_remaining: -1,
          unlimited: true,
        },
    activity: {
      thread_count: threadCount,
      last_activity_at: iso(0.3),
    },
  };
}

const S3_PLAYBOOK = `---
- name: Provision versioned S3 bucket
  hosts: localhost
  gather_facts: false
  vars:
    bucket_name: "{{ org_prefix }}-app-artifacts"
    aws_region: eu-west-1
  tasks:
    - name: Create S3 bucket with versioning
      amazon.aws.s3_bucket:
        name: "{{ bucket_name }}"
        state: present
        region: "{{ aws_region }}"
        versioning: true
        encryption: AES256
        public_access:
          block_public_acls: true
          block_public_policy: true
          ignore_public_acls: true
          restrict_public_buckets: true
        tags:
          project: ansibleai-lab
          env: staging
`;

const K8S_PLAYBOOK = `---
- name: Scale nginx deployment
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Ensure Deployment has 3 replicas
      kubernetes.core.k8s:
        state: present
        definition:
          apiVersion: apps/v1
          kind: Deployment
          metadata:
            name: nginx
            namespace: production
          spec:
            replicas: 3
            selector:
              matchLabels:
                app: nginx
            template:
              metadata:
                labels:
                  app: nginx
              spec:
                containers:
                  - name: nginx
                    image: nginx:1.27
                    ports:
                      - containerPort: 80
`;

const NGINX_PLAYBOOK = `---
- name: Install nginx
  hosts: web
  become: true
  tasks:
    - name: Install nginx package
      ansible.builtin.package:
        name: nginx
        state: present
    - name: Enable and start nginx
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true
`;

function msg(
  id: number,
  threadId: number,
  role: 'user' | 'assistant',
  content: string,
  extra: Partial<ChatMessage> = {},
  hoursAgo: number,
): ChatMessage {
  return {
    id,
    thread_id: threadId,
    role,
    content,
    ts: iso(hoursAgo),
    playbook: extra.playbook ?? null,
    filename: extra.filename ?? null,
    module: extra.module ?? null,
    validation: extra.validation ?? null,
    module_ref: extra.module_ref ?? null,
    rag_meta: extra.rag_meta ?? null,
    tool_trace: extra.tool_trace ?? null,
  };
}

const s3Messages: ChatMessage[] = [
  msg(
    1001,
    MOCK_THREAD.s3,
    'user',
    'Create an AWS S3 bucket with versioning enabled, default encryption, and all public access blocked. Name it from org_prefix.',
    {},
    6,
  ),
  msg(
    1002,
    MOCK_THREAD.s3,
    'assistant',
    'Grounded on **amazon.aws.s3_bucket**. The playbook creates the bucket, turns versioning on, sets AES256 encryption, and blocks public ACLs. Substitute `org_prefix` before you run it.',
    {
      playbook: S3_PLAYBOOK,
      filename: 'playbook_s3_versioned_bucket.yml',
      module: 'amazon.aws.s3_bucket',
      validation: {
        is_valid: true,
        passed: 8,
        passed_msgs: ['YAML syntax', 'Playbook structure', 'FQCN module', 'Required params'],
        warnings: [],
        errors: [],
        ansible_lint: { status: 'passed', violations: [], backend: 'native' },
        module: 'amazon.aws.s3_bucket',
      },
      module_ref: {
        module: 'amazon.aws.s3_bucket',
        found: true,
        sources: [
          {
            module: 'amazon.aws.s3_bucket',
            found: true,
            category: 'amazon.aws',
            total_params: 28,
            retrieval_rank: 1,
            retrieval_top_score: 0.91,
            is_playbook_module: true,
            is_rag_primary: true,
            description: 'Manage S3 buckets, versioning, encryption, and public access block settings.',
            doc_url: 'https://docs.ansible.com/ansible/latest/collections/amazon/aws/s3_bucket_module.html',
            required_params: [{ name: 'name', type: 'str' }],
            optional_params: [
              { name: 'versioning', type: 'bool' },
              { name: 'encryption', type: 'str' },
            ],
          },
        ],
      },
      rag_meta: {
        primary_module: 'amazon.aws.s3_bucket',
        primary_collection: 'amazon.aws',
        primary_score: 0.91,
        chunks: 6,
        source_url:
          'https://docs.ansible.com/ansible/latest/collections/amazon/aws/s3_bucket_module.html',
        intent: 'generate_playbook',
      },
      tool_trace: [
        { tool: 'search_docs', result: { primary_module: 'amazon.aws.s3_bucket', chunks: 6 } },
        { tool: 'draft_playbook', result: { module: 'amazon.aws.s3_bucket', yaml_chars: 720 } },
        { tool: 'gate', result: { ready: true, errors: 0, ansible_lint: 'passed', ansible_lint_violations: 0 } },
      ],
    },
    5.9,
  ),
];

const k8sMessages: ChatMessage[] = [
  msg(2001, MOCK_THREAD.k8s, 'user', 'Scale the nginx Deployment in the production namespace to 3 replicas.', {}, 4),
  msg(
    2002,
    MOCK_THREAD.k8s,
    'assistant',
    'Using **kubernetes.core.k8s** with a full Deployment `definition`. Replicas are set to 3; selector and labels stay on `app: nginx`.',
    {
      playbook: K8S_PLAYBOOK,
      filename: 'playbook_k8s_scale_nginx.yml',
      module: 'kubernetes.core.k8s',
      validation: {
        is_valid: true,
        passed: 7,
        passed_msgs: ['YAML syntax', 'k8s definition layout', 'FQCN'],
        warnings: ['Image tag is not pinned by digest'],
        errors: [],
        ansible_lint: { status: 'passed', violations: [], backend: 'native' },
        module: 'kubernetes.core.k8s',
      },
      rag_meta: {
        primary_module: 'kubernetes.core.k8s',
        primary_collection: 'kubernetes.core',
        primary_score: 0.88,
        chunks: 8,
        intent: 'generate_playbook',
      },
      tool_trace: [
        { tool: 'search_docs', result: { primary_module: 'kubernetes.core.k8s' } },
        { tool: 'draft_playbook', result: { module: 'kubernetes.core.k8s', yaml_chars: 890 } },
        { tool: 'gate', result: { ready: true, errors: 0, ansible_lint: 'passed' } },
      ],
    },
    3.9,
  ),
];

const nginxMessages: ChatMessage[] = [
  msg(
    3001,
    MOCK_THREAD.nginx,
    'user',
    'Install and configure Nginx on the web group, then enable the service.',
    {},
    0.05,
  ),
];

const postgresMessages: ChatMessage[] = [
  msg(
    4001,
    MOCK_THREAD.postgres,
    'user',
    'Write a playbook that dumps PostgreSQL nightly to /var/backups/pg with rotation.',
    {},
    8,
  ),
  msg(
    4002,
    MOCK_THREAD.postgres,
    'assistant',
    'Generation failed before an answer could be produced. The production gate reported missing required parameters for `community.general.cron` and ansible-lint returned `syntax-check` errors. Try narrowing the request to a single host group and an explicit database name.',
    {
      validation: {
        is_valid: false,
        passed: 2,
        passed_msgs: ['YAML syntax'],
        warnings: [],
        errors: ['Missing required params: [name, job]', 'Module community.postgresql.postgresql_db not resolved'],
        ansible_lint: {
          status: 'violations',
          violations: ['syntax-check[unknown-module]: community.postgresql.postgresql_db'],
          backend: 'native',
        },
      },
      tool_trace: [
        { tool: 'search_docs', result: { primary_module: 'community.general.cron' } },
        { tool: 'draft_playbook', result: { yaml_chars: 410 } },
        { tool: 'gate', result: { ready: false, errors: 2, ansible_lint: 'violations', ansible_lint_violations: 1 } },
      ],
    },
    7.9,
  ),
];

const deployMessages: ChatMessage[] = [
  msg(5001, MOCK_THREAD.deploy, 'user', 'Deploy the billing API to Kubernetes with a Service and Ingress.', {}, 2),
  msg(
    5002,
    MOCK_THREAD.deploy,
    'assistant',
    'Generation stopped. Send a new message whenever you want to try again.',
    {},
    1.95,
  ),
];

const azureMessages: ChatMessage[] = [
  msg(
    6001,
    MOCK_THREAD.azure,
    'user',
    'Provision an Azure VM for a small web app.',
    {},
    1,
  ),
  msg(
    6002,
    MOCK_THREAD.azure,
    'assistant',
    'I can draft this, but a few details are missing:\n\n- Which image (Ubuntu 22.04, RHEL, Windows)?\n- Resource group name, or should the playbook create one?\n- SSH key path vs password auth?\n\nReply with those and I will generate `azure.azcollection.azure_rm_virtualmachine`.',
    {
      rag_meta: {
        primary_module: 'azure.azcollection.azure_rm_virtualmachine',
        primary_collection: 'azure.azcollection',
        primary_score: 0.79,
        chunks: 5,
        intent: 'generate_playbook',
        awaiting_user: true,
      },
      tool_trace: [
        { tool: 'search_docs', result: { primary_module: 'azure.azcollection.azure_rm_virtualmachine' } },
        { tool: 'clarify_decider', result: { questions: 3 } },
      ],
    },
    0.95,
  ),
];

export function cloneMessages(rows: ChatMessage[]): ChatMessage[] {
  return rows.map((m) => ({ ...m, tool_trace: m.tool_trace ? [...m.tool_trace] : m.tool_trace }));
}

export const mockThreadSeeds: Thread[] = [
  {
    id: MOCK_THREAD.s3,
    title: 'Create an AWS S3 bucket with versioning',
    created_at: iso(6.1),
    updated_at: iso(5.9),
    message_count: 2,
    messages: s3Messages,
  },
  {
    id: MOCK_THREAD.k8s,
    title: 'Configure Kubernetes deployment',
    created_at: iso(4.1),
    updated_at: iso(3.9),
    message_count: 2,
    messages: k8sMessages,
  },
  {
    id: MOCK_THREAD.nginx,
    title: 'Install and configure Nginx',
    created_at: iso(0.06),
    updated_at: iso(0.05),
    message_count: 1,
    messages: nginxMessages,
  },
  {
    id: MOCK_THREAD.postgres,
    title: 'Create a PostgreSQL backup playbook',
    created_at: iso(8.1),
    updated_at: iso(7.9),
    message_count: 2,
    messages: postgresMessages,
  },
  {
    id: MOCK_THREAD.deploy,
    title: 'Deploy an application to Kubernetes',
    created_at: iso(2.1),
    updated_at: iso(1.95),
    message_count: 2,
    messages: deployMessages,
  },
  {
    id: MOCK_THREAD.azure,
    title: 'Provision an Azure VM',
    created_at: iso(1.1),
    updated_at: iso(0.95),
    message_count: 2,
    messages: azureMessages,
  },
];

export const mockStats: StatsPayload = {
  total: 48,
  valid: 37,
  invalid: 11,
  warns: 9,
  modules: [
    { module: 'kubernetes.core.k8s', count: 14 },
    { module: 'amazon.aws.s3_bucket', count: 8 },
    { module: 'ansible.builtin.copy', count: 7 },
    { module: 'azure.azcollection.azure_rm_virtualmachine', count: 6 },
    { module: 'ansible.builtin.service', count: 5 },
    { module: 'community.general.cron', count: 4 },
    { module: 'amazon.aws.ec2_instance', count: 4 },
  ],
};

export const mockRagReady: RagStatus = { available: true, chunks: 8124 };
export const mockRagOffline: RagStatus = { available: false, chunks: 0 };

export const mockDocsHealthy: DocsStatus = {
  kb_metadata: { generated_at: iso(24 * 3), total_modules: 186 },
  module_health: [
    { slug: 'amazon.aws::s3_bucket', param_count: 28, example_count: 6, required_count: 1, health_score: 94 },
    { slug: 'kubernetes.core::k8s', param_count: 22, example_count: 8, required_count: 0, health_score: 91 },
    { slug: 'azure.azcollection::azure_rm_virtualmachine', param_count: 40, example_count: 4, required_count: 3, health_score: 78 },
    { slug: 'community.general::cron', param_count: 14, example_count: 3, required_count: 2, health_score: 72 },
    { slug: 'ansible.builtin::copy', param_count: 18, example_count: 5, required_count: 1, health_score: 88 },
    { slug: 'amazon.aws::ec2_instance', param_count: 35, example_count: 2, required_count: 2, health_score: 64 },
    { slug: 'kubernetes.core::helm', param_count: 16, example_count: 3, required_count: 1, health_score: 81 },
    { slug: 'ansible.builtin::file', param_count: 12, example_count: 4, required_count: 1, health_score: 86 },
  ],
};

export const mockDocsEmpty: DocsStatus = {
  kb_metadata: { generated_at: undefined, total_modules: 0 },
  module_health: [],
};

export const mockRollback: RollbackVersion[] = [
  { filename: 'kb_2026-08-18T09-12-00Z', modified_at: iso(24 * 3), size: 2_480_000 },
  { filename: 'kb_2026-08-11T03-00-00Z', modified_at: iso(24 * 10), size: 2_310_000 },
  { filename: 'kb_2026-07-28T16-44-00Z', modified_at: iso(24 * 24), size: 1_980_000 },
];

export const mockScrapeSessions: ScrapeSession[] = [
  {
    id: 44,
    triggered_at: iso(24 * 3),
    status: 'success',
    summary: {
      diffs: [
        { module_slug: 'amazon.aws::s3_bucket', diff_summary: '+2 params · examples unchanged', health_score: 94 },
        { module_slug: 'kubernetes.core::k8s', diff_summary: 'description refresh', health_score: 91 },
      ],
    },
  },
  {
    id: 43,
    triggered_at: iso(24 * 10),
    status: 'partial',
    summary: {
      changed: [
        { slug: 'azure.azcollection::azure_rm_virtualmachine', remote_hash: 'a1b2c3d4e5', local_hash: '9988776655' },
      ],
      diffs: [
        { slug: 'azure.azcollection::azure_rm_virtualmachine', diff_summary: 'required_params +1', health_score: 78 },
      ],
    },
  },
];

export const mockChangedModules = [
  { slug: 'amazon.aws::ec2_instance', remote_hash: 'c0ffee1234abcd', local_hash: 'deadbeef0001' },
  { slug: 'community.general::cron', remote_hash: '111122223333', local_hash: 'aaaabbbbcccc' },
];

export const mockScrapeLogLines = [
  'Fetching index modules list...',
  'Checking amazon.aws::s3_bucket ...',
  '  -> unchanged',
  'Checking amazon.aws::ec2_instance ...',
  '  -> changed (remote != local)',
  'Checking kubernetes.core::k8s ...',
  '  -> unchanged',
  'Checking community.general::cron ...',
  '  -> changed (remote != local)',
  'Done. changed=2 unchanged=4 failed=0',
  'STREAM_END',
];

export const mockFailedScrapeLines = [
  'Fetching index modules list...',
  'Checking kubernetes.core::k8s ...',
  '  -> failed: HTTPSConnectionPool(host=docs.ansible.com): timed out',
  'Done. changed=0 unchanged=0 failed=1',
  'STREAM_END',
];

export const mockGeneratingThoughts: ThoughtEntry[] = [
  { id: 0, step: 'planning', text: 'Intent: generate_playbook · collection amazon.aws', at: Date.now() - 18_000 },
  { id: 1, step: 'retrieving', text: 'search_docs', detail: 'amazon.aws.package · 6 chunks', at: Date.now() - 12_000 },
  { id: 2, step: 'generating', text: 'draft_playbook', detail: 'Writing tasks for nginx + service', at: Date.now() - 4_000 },
];

export const mockProgressStep: GenerationStep = 'generating';

export const cannedAssistantReply: ChatMessage = msg(
  9002,
  0,
  'assistant',
  'Here is a first draft grounded on **ansible.builtin.package** and **ansible.builtin.service**. Review hosts and then run with `--check`.',
  {
    playbook: NGINX_PLAYBOOK,
    filename: 'playbook_install_nginx.yml',
    module: 'ansible.builtin.package',
    validation: {
      is_valid: true,
      passed: 6,
      passed_msgs: ['YAML syntax', 'FQCN', 'Required params'],
      warnings: [],
      errors: [],
      ansible_lint: { status: 'passed', violations: [] },
    },
    rag_meta: {
      primary_module: 'ansible.builtin.package',
      primary_collection: 'ansible.builtin',
      chunks: 4,
      intent: 'generate_playbook',
    },
    tool_trace: [
      { tool: 'search_docs', result: { primary_module: 'ansible.builtin.package' } },
      { tool: 'draft_playbook', result: { yaml_chars: 420 } },
      { tool: 'gate', result: { ready: true, errors: 0, ansible_lint: 'passed' } },
    ],
  },
  0,
);
