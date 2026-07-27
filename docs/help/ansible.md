# Ansible Integration

Ansible is used by strata to configure servers after infrastructure is provisioned
(configuration management phase). strata invokes `ansible-playbook` as a subprocess.

Installation
- macOS: `brew install ansible`
- Linux: `pip install ansible` or via package manager (`apt install ansible`, `dnf install ansible`)
- Windows (WSL recommended): `pip install ansible` inside WSL
- Docs: https://docs.ansible.com/ansible/latest/installation_guide/

Verify install
```
ansible-playbook --version
```

Configuration YAML

```yaml
integrations:
  - name: ansible
    type: ansible
    capabilities: [infrastructure]
    required: false
    validation:
      command: ansible-playbook --version
      min_version: "2.12.0"
```

Authentication
- **SSH keys** (most common) — add your private key to `ssh-agent` or set `ansible_ssh_private_key_file` in inventory. For strata deployments the key is resolved from the secret store at deploy time — see the workspace YAML `configuration.ssh_private_key_secret`.
- **Password** — set `ANSIBLE_BECOME_PASS` or use Ansible Vault for passwords.
- **Become (sudo)** — configure `ansible_become_pass` in vars or via `ANSIBLE_BECOME_PASS`.

Environment variables

| Variable              | Purpose                                                                    |
| --------------------- | -------------------------------------------------------------------------- |
| `ANSIBLE_CONFIG`      | Path to `ansible.cfg` (default: current dir or `/etc/ansible/ansible.cfg`) |
| `ANSIBLE_INVENTORY`   | Default inventory file or directory                                        |
| `ANSIBLE_BECOME_PASS` | Privilege escalation password                                              |
| `ANSIBLE_ROLES_PATH`  | Additional roles search paths                                              |

Connection parameters in workspace YAML

```yaml
provisioners:
  - name: configure
    provisioner: ansible
    source:
      repository: my-repo
      source_path: ansible
    configuration:
      playbook: site.yml
      inventory: inventory/hosts.yml
      ssh_private_key_secret: my_ssh_key   # secret name resolved at deploy time
      extra_vars:
        env: production
```

SSH key handling
- Never put SSH private keys directly in YAML.
- Declare the key in `spec.secrets` with a reference to the secret store.
- strata writes the key to a `chmod 600` temp file for the subprocess duration, then deletes it.

Common checks
```
ansible-playbook site.yml --check         # dry run
ansible-playbook site.yml --list-tasks    # show tasks without running
ansible-inventory -i inventory/ --list    # verify inventory
```

Docs
- https://docs.ansible.com
