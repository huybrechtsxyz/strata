# Network

Virtual network topology definition (VPC, VNet, subnets, routing).

A network (`kind: network`) describes:
- **VPC / VNet** — top-level network container
- **Subnets** — address ranges for resource placement
- **Route tables** — traffic routing rules
- **Gateways** — NAT, Internet Gateway, VPN endpoints
- **DNS** — nameservers and resolution

Network definitions are cloud-agnostic YAML that translate to provider-specific
resources (AWS VPC, Azure VNet, GCP VPC, etc.).

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: production-vpc
spec:
  type: vpc
  cidr_block: 10.0.0.0/16
  availability_zones: [us-east-1a, us-east-1b, us-east-1c]
  
  subnets:
    public:
      - cidr: 10.0.1.0/24
        zone: us-east-1a
      - cidr: 10.0.2.0/24
        zone: us-east-1b
    private:
      - cidr: 10.0.10.0/24
        zone: us-east-1a
      - cidr: 10.0.11.0/24
        zone: us-east-1b
  
  route_tables:
    public:
      - destination: 0.0.0.0/0
        target: internet_gateway
    private:
      - destination: 0.0.0.0/0
        target: nat_gateway
  
  gateways:
    internet:
      type: igw
    nat:
      type: nat
      public_subnet: 10.0.1.0/24
```

---

## Cross-Region Networks

Define multiple networks for HA:

```yaml
spec:
  regions:
    us-east-1:
      cidr_block: 10.0.0.0/16
    eu-west-1:
      cidr_block: 10.1.0.0/16
```

---

## Peering and VPN

Connect networks:

```yaml
spec:
  peerings:
    - name: prod-staging
      remote_network: @config/networks/staging-vpc.yaml
      cidr: 10.1.0.0/16
  
  vpn:
    - name: on-premises
      remote_cidr: 192.168.0.0/16
      authentication: ipsec
```

---

## Environment-Specific Topology

Override per environment:

```yaml
# environments/staging.yaml
spec:
  network_overrides:
    production-vpc:
      cidr_block: 10.100.0.0/16
      availability_zones: [us-east-1a, us-east-1b]
```

---

## See Also

- `firewall` — security rules for networks
- `provider` — cloud provider and region
- `workspace` — top-level blueprint containing networks
