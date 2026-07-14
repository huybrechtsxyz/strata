

############
strata
############

.. image:: _static/python-blue.svg
        :target: https://www.python.org/
        :alt: Python 3.13

.. toctree::
   :maxdepth: 2
   :caption: Getting Started:

   README
   INDEX

.. toctree::
   :maxdepth: 2
   :caption: VS Code Extension:

   vscode/README
   vscode/publish
   vscode/installation
   vscode/features
   vscode/tree-views
   vscode/diagnostics
   vscode/codelens
   vscode/chat
   vscode/settings
   vscode/keyboard-shortcuts

.. toctree::
   :maxdepth: 2
   :caption: MCP Server for AI Agents:

   mcp/README
   mcp/setup-and-installation
   mcp/claude-deployment-assistant
   mcp/copilot-integration
   mcp/security-and-workflows
   mcp/ai-troubleshooting
   mcp/tools-reference

.. toctree::
   :maxdepth: 2
   :caption: Platform Reference:

   platform/readme
   platform/getting-started
   platform/value-proposition
   platform/commands
   platform/workflow
   platform/cli-preferences
   platform/ci-integration
   platform/architecture
   platform/exit-codes
   platform/manifest-cli
   platform/manifest-schema
   platform/sbom-plugin-api
   platform/provisioner-plugin-api

.. toctree::
   :maxdepth: 2
   :caption: Internals:

   platform/models
   platform/services
   platform/configuration
   platform/integrations
   platform/mcp
   platform/builders
   platform/deployers
   platform/validators
   platform/lifecycles
   platform/policies
   platform/exceptions
   platform/logging
   platform/utilities

.. toctree::
   :maxdepth: 2
   :caption: Config File Formats:

   config/readme
   config/configuration
   config/workspace
   config/deployment
   config/environment
   config/provider
   config/resource
   config/firewall
   config/module
   config/namespace
   config/dns
   config/network
   config/manifest
   config/tenant
   config/workflow

.. toctree::
   :maxdepth: 2
   :caption: Platform Examples:

   examples/readme
   examples/azure-aks
   examples/aws-eks
   examples/gcp-gke
   examples/hetzner-compose
   examples/kamatera-swarm

.. toctree::
   :maxdepth: 2
   :caption: CLI Reference:

.. toctree::
   :maxdepth: 1
   :caption: Architectural Decisions:

   decisions/README
   decisions/0001-kubernetes-style-yaml-schema
   decisions/0002-python-click-not-compiled-cli
   decisions/0003-layered-architecture
   decisions/0004-exit-code-convention
   decisions/0005-secret-resolution-at-build-time
   decisions/0006-policy-engine-for-deployment-guardrails
   decisions/0007-deployment-state-locking
   decisions/0008-infrastructure-drift-detection
   decisions/0009-sbom-extended-sources-and-inventory
   decisions/0010-rename-configuration-repositories-to-remotes
   decisions/0011-promotion-strategies-for-version-progression
   decisions/0012-rename-customer-to-tenant
   decisions/0013-auto-generated-secrets
   decisions/0014-onboarding-experience
   decisions/0015-flow-command-dependency-graph
   decisions/0016-console-interactive-repl
   decisions/0017-jinja2-template-engine
   decisions/0017-tag-based-release-workflow-option-c
   decisions/0018-deployment-audit-traceability
   decisions/0019-configurable-terraform-build-output
   decisions/0020-lifecycle-phases-and-environment-variables
   decisions/0021-deployment-manifests-as-first-class-build-artifacts
   decisions/0022-siem-integration-splunk-hec-cef
   decisions/0023-pluggable-provisioner-framework
   decisions/0024-environment-composition-flat-merge-fix
   decisions/0025-ai-agent-integration-for-build-and-deploy
   decisions/0026-resolved-model-cache
   decisions/0027-command-timeout-for-long-running-operations
   decisions/0028-sigterm-graceful-shutdown-and-lock-release
   decisions/0029-realtime-progress-streaming-ndjson
   decisions/0030-command-lifecycle-explicitness-and-thin-overrides
   decisions/0031-cost-estimation-and-visibility
   decisions/0032-approval-workflows-and-gates
   decisions/0033-github-pull-request-integration
   decisions/0034-diagram-visualization-in-vscode-extension
   decisions/0035-enterprise-store
   decisions/0036-workspace-provider-environment-overrides
   decisions/0037-mass-wave-deployment

.. toctree::
   :maxdepth: 2
   :caption: Guides:

   guides/how-deployments-work
   guides/how-deployment-locking-works
   guides/helm-modules
   guides/features
   guides/faq
   guides/config-faq
   guides/cookbook-add-environment
   guides/environment-composition
   guides/pattern-cross-env-changes
   guides/troubleshooting-what-changed
   guides/setup-azure-oidc
   guides/deployment-manifests
   guides/compliance-and-deployment-manifests
   guides/siem-audit-forwarding
   guides/extending-sbom-plugins
   guides/sbom-plugin-examples
   guides/cve-vulnerability-scanning
   guides/at-scale
   guides/using-console
   guides/building-a-provisioner-plugin
   guides/detecting-infrastructure-drift

.. toctree::
   :maxdepth: 1
   :caption: Skills:

   skills/strata-onboarding

.. :orphan:

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`



