# Helm Integration

Helm is the Kubernetes package manager. strata uses the `helm` CLI for deploying
charts to Kubernetes clusters as part of the `container` capability.

Installation
- macOS: `brew install helm`
- Linux (script): `curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash`
- Windows (Scoop): `scoop install helm`
- Windows (Chocolatey): `choco install kubernetes-helm`
- Docs: https://helm.sh/docs/intro/install/

Verify install
```
helm version
```

Minimum recommended version: 3.10.0

Prerequisites
- A running Kubernetes cluster accessible via `kubectl`
- A valid `kubeconfig` file (default: `~/.kube/config`)

Configuration YAML

```yaml
integrations:
  - name: helm
    type: helm
    capabilities: [infrastructure]
    required: false
    validation:
      command: helm version
      min_version: "3.10.0"
```

Authentication
Helm uses the kubeconfig file for cluster authentication — no additional Helm-specific
credentials are needed.

| Variable     | Purpose                 | Required                       |
| ------------ | ----------------------- | ------------------------------ |
| `KUBECONFIG` | Path to kubeconfig file | No (default: `~/.kube/config`) |

Cloud-specific cluster access setup:
- **Azure AKS**: `az aks get-credentials --resource-group <rg> --name <cluster>`
- **AWS EKS**: `aws eks update-kubeconfig --name <cluster> --region <region>`
- **GCP GKE**: `gcloud container clusters get-credentials <cluster> --zone <zone>`

Common commands
```
helm repo add stable https://charts.helm.sh/stable
helm repo update
helm install my-release stable/nginx-ingress
helm upgrade my-release stable/nginx-ingress
helm list
helm status my-release
helm uninstall my-release
```

Docs
- https://helm.sh/docs
