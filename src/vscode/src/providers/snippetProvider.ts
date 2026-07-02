/**
 * SnippetProvider — YAML boilerplate completions for strata document kinds.
 *
 * Trigger: type `strata:` in any YAML file.
 * VS Code shows a completion item for every known kind.  Selecting one
 * replaces the `strata:<typed>` prefix with a full, tabstop-annotated
 * document skeleton.
 *
 * Trigger character registered in extension.ts:
 *   vscode.languages.registerCompletionItemProvider({ language: 'yaml' }, provider, ':')
 *
 * Snippet tab-stop conventions:
 *   ${1:name}     — document name (always first)
 *   ${2:...}      — description
 *   ${N:...}      — primary spec fields in document order
 *   $0            — final cursor position (after spec block)
 */

import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// Snippet definitions
// ---------------------------------------------------------------------------

interface SnippetDef {
    kind: string;
    description: string;
    body: string;
}

const API_VERSION = 'strata.huybrechts.xyz/v1';

const SNIPPETS: SnippetDef[] = [
    // ── workspace ────────────────────────────────────────────────────────────
    {
        kind: 'workspace',
        description: 'Top-level workspace — declares providers, provisioners, topology, and resources.',
        body: `apiVersion: ${API_VERSION}
kind: workspace
meta:
  name: \${1:my_workspace}
  annotations:
    description: \${2:Workspace description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, tag2}]
spec:
  providers:
    - name: \${5:my_provider}
      file: "@\${6:repo}/stack/\${7:provider}.yaml"

  provisioners:
    - name: \${8:terraform}
      provisioner: terraform
      source:
        repository: \${9:my_repo}
        source_path: \${10:terraform}

  topology:
    - name: \${11:my_cluster}
      provider: \${5:my_provider}
      provisioner: \${8:terraform}
      type: \${12:kubernetes}
      components:
        - resource: \${13:my_resource}
      namespaces:
        - namespace: \${14:my_namespace}

  resources:
    - name: \${13:my_resource}
      file: "@\${6:repo}/stack/\${15:resource}.yaml"
      description: \${16:Resource description}
$0`,
    },

    // ── configuration ────────────────────────────────────────────────────────
    {
        kind: 'configuration',
        description: 'Configuration — layering strategy, deployment properties, remotes, and integrations.',
        body: `apiVersion: ${API_VERSION}
kind: configuration
meta:
  name: \${1:my_config}
  annotations:
    description: \${2:Configuration description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, tag2}]
spec:
  layering:
    - name: \${5:environment}
      description: "\${6:Deployment environment (dev, prd)}"
      required: true

  deployment:
    additional_properties: false
    properties:
      \${5:environment}:
        pattern: "^\${7:(dev|prd)}$"
        required: true
        description: "\${8:Target environment}"

  remotes:
    - name: \${9:my_repo}
      type: gitops
      repository: \${10:git@github.com:your-org/repo.git}
      reference: \${11:main}
      source_path: .
      deploy_path: .

  integrations:
    - name: git
      type: git
      capabilities: [repository]
      required: true
$0`,
    },

    // ── deployment ───────────────────────────────────────────────────────────
    {
        kind: 'deployment',
        description: 'Deployment — orchestrates build and deploy stages across environments.',
        body: `apiVersion: ${API_VERSION}
kind: deployment
meta:
  name: \${1:my_deployment}
  annotations:
    description: \${2:Deployment description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, tag2}]
spec:
  layers:
    environment: \${5:dev}

  properties:
    environment: \${5:dev}

  workspace:
    name: \${6:my_workspace}
    description: \${7:Workspace description}
    file: "@\${8:repo}/stack/\${9:workspace}.yaml"

  environments:
    - "@\${8:repo}/environments/\${10:env-dev}.yaml"

  stages:
    - name: \${11:infrastructure}
      provisioner: \${12:terraform}
      scope: all
      on_failure: stop

    - name: \${13:platform}
      provisioner: \${14:helm}
      scope: all
      depends_on: [\${11:infrastructure}]
      on_failure: stop
$0`,
    },

    // ── environment ──────────────────────────────────────────────────────────
    {
        kind: 'environment',
        description: 'Environment — variables and secrets for a deployment target.',
        body: `apiVersion: ${API_VERSION}
kind: environment
meta:
  name: \${1:my_env_dev}
  annotations:
    description: \${2:Development environment}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:dev, environment}]
spec:
  variables:
    - key: \${5:WORKSPACE}
      store: constant
      value: \${6:my_workspace}
    - key: \${7:ENVIRONMENT}
      store: constant
      value: \${8:dev}
    - key: \${9:MY_VAR}
      store: environment
      value: \${9:MY_VAR}

  secrets:
    - key: \${10:MY_SECRET}
      store: environment
      value: \${10:MY_SECRET}
$0`,
    },

    // ── module ───────────────────────────────────────────────────────────────
    {
        kind: 'module',
        description: 'Module — a deployable service unit (Helm chart, Compose service, etc.).',
        body: `apiVersion: ${API_VERSION}
kind: module
meta:
  name: \${1:my_module}
  annotations:
    description: \${2:Module description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, module}]
spec:
  type: \${5|helm,compose,script|}
  source:
    chart_repository: \${6:https://charts.example.com}
    chart_name: \${7:my-chart}
    chart_version: "\${8:1.0.0}"
  kubernetes_namespace: \${9:default}
  services:
    - name: \${10:my-service}
      configuration:
        replicaCount: \${11:1}
$0`,
    },

    // ── namespace ────────────────────────────────────────────────────────────
    {
        kind: 'namespace',
        description: 'Namespace — groups modules that share a Kubernetes namespace.',
        body: `apiVersion: ${API_VERSION}
kind: namespace
meta:
  name: \${1:my_namespace}
  annotations:
    description: \${2:Namespace description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, namespace}]
spec:
  modules:
    - name: \${5:my_module}
      file: "@\${6:repo}/stack/\${7:module}.yaml"
$0`,
    },

    // ── provider ─────────────────────────────────────────────────────────────
    {
        kind: 'provider',
        description: 'Provider — cloud or infrastructure target (region, account, cluster).',
        body: `apiVersion: ${API_VERSION}
kind: provider
meta:
  name: \${1:my_provider}
  annotations:
    description: \${2:Provider description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, provider}]
spec:
  properties:
    type: \${5|azure,aws,gcp,hetzner,kamatera|}
    region: \${6:westeurope}
$0`,
    },

    // ── resource ─────────────────────────────────────────────────────────────
    {
        kind: 'resource',
        description: 'Resource — a single infrastructure resource (VM, cluster, database, etc.).',
        body: `apiVersion: ${API_VERSION}
kind: resource
meta:
  name: \${1:my_resource}
  annotations:
    description: \${2:Resource description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, resource}]
spec:
  properties:
    provider_type: \${5|azure,aws,gcp,hetzner|}
    resource_type: \${6:my_resource_type}
    category: \${7:compute}
    subcategory: \${8:virtual_machine}
  configuration:
    \${9:key}: \${10:value}
$0`,
    },

    // ── network ──────────────────────────────────────────────────────────────
    {
        kind: 'network',
        description: 'Network — virtual network, address spaces, and subnets.',
        body: `apiVersion: ${API_VERSION}
kind: network
meta:
  name: \${1:my_network}
  annotations:
    description: \${2:Network description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, network}]
spec:
  networks:
    - name: \${5:vnet_main}
      description: \${6:Main virtual network}
      address_space:
        - value: "\${7:10.0.0.0/16}"
      subnets:
        - name: \${8:snet_primary}
          cidr:
            value: "\${9:10.0.0.0/20}"
          description: \${10:Primary subnet}
$0`,
    },

    // ── firewall ─────────────────────────────────────────────────────────────
    {
        kind: 'firewall',
        description: 'Firewall — inbound and outbound rules for a host or cluster.',
        body: `apiVersion: ${API_VERSION}
kind: firewall
meta:
  name: \${1:my_firewall}
  annotations:
    description: \${2:Firewall rules}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, firewall}]
spec:
  reset: true
  defaults:
    - direction: in
      permission: deny
      comment: Deny all incoming by default
    - direction: out
      permission: deny
      comment: Deny all outgoing by default
  allow:
    - direction: out
      proto: udp
      port: 53
      comment: DNS (UDP)
    - direction: out
      proto: tcp
      port: 53
      comment: DNS (TCP)
    - direction: in
      proto: tcp
      port: 22
      comment: SSH
    - direction: in
      proto: tcp
      port: \${5:443}
      comment: \${6:HTTPS}
$0`,
    },

    // ── dns ──────────────────────────────────────────────────────────────────
    {
        kind: 'dns',
        description: 'DNS — zones and records managed by a DNS provider.',
        body: `apiVersion: ${API_VERSION}
kind: dns
meta:
  name: \${1:my_dns}
  annotations:
    description: \${2:DNS records for platform services}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, dns}]
spec:
  provider: \${5|inwx,cloudflare,route53,azure_dns|}
  references:
    variables:
      - \${6:SERVER_IP}
  zones:
    - name: \${7:example.com}
      ttl: \${8:3600}
      records:
        - name: "@"
          type: A
          var: \${6:SERVER_IP}
        - name: "www"
          type: CNAME
          value: "\${7:example.com}."
        - name: "\${9:api}"
          type: A
          var: \${6:SERVER_IP}
$0`,
    },

    // ── tenant ───────────────────────────────────────────────────────────────
    {
        kind: 'tenant',
        description: 'Tenant — a customer or organisational unit with its own configuration scope.',
        body: `apiVersion: ${API_VERSION}
kind: tenant
meta:
  name: \${1:my_tenant}
  annotations:
    description: \${2:Tenant description}
  labels:
    version: "\${3:1.0.0}"
  tags: [\${4:tag1, tenant}]
spec:
  properties:
    display_name: "\${5:My Tenant}"
    contact_email: "\${6:admin@example.com}"
  configuration:
    \${7:key}: \${8:value}
$0`,
    },
];

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class SnippetProvider implements vscode.CompletionItemProvider, vscode.Disposable {

    private _disposables: vscode.Disposable[] = [];

    register(context: vscode.ExtensionContext): void {
        this._disposables.push(
            vscode.languages.registerCompletionItemProvider(
                { language: 'yaml' },
                this,
                ':',   // trigger character — fires when user types ':'
            ),
        );
        context.subscriptions.push(...this._disposables);
    }

    dispose(): void {
        this._disposables.forEach((d) => d.dispose());
        this._disposables = [];
    }

    // -------------------------------------------------------------------------
    // CompletionItemProvider
    // -------------------------------------------------------------------------

    provideCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): vscode.CompletionItem[] | null {
        const lineText = document.lineAt(position).text;
        const linePrefix = lineText.substring(0, position.character);

        // Only activate when the line up-to-cursor matches `strata:` (with
        // optional leading whitespace) followed by an optional partial kind.
        const m = linePrefix.match(/(?:^|\s)strata:([a-z]*)$/);
        if (!m) return null;

        const typed = m[1];                             // what was typed after `:`
        const triggerStart = linePrefix.lastIndexOf('strata:');
        const replaceRange = new vscode.Range(
            position.line, triggerStart,
            position.line, position.character,
        );

        return SNIPPETS
            .filter((s) => s.kind.startsWith(typed))
            .map((s) => {
                const item = new vscode.CompletionItem(
                    `strata:${s.kind}`,
                    vscode.CompletionItemKind.Snippet,
                );
                item.detail = `Strata ${s.kind} boilerplate`;
                item.documentation = new vscode.MarkdownString(s.description);
                item.insertText = new vscode.SnippetString(s.body);
                item.range = replaceRange;
                // Float strata snippets to top of the completion list
                item.sortText = `0_strata_${s.kind}`;
                item.filterText = `strata:${s.kind}`;
                return item;
            });
    }
}
