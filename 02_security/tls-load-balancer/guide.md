# Terminating TLS at an AWS Load Balancer — Parameter Walkthrough

This guide walks through every parameter you will touch when terminating TLS
at an Application Load Balancer (ALB) with a certificate from AWS Certificate
Manager (ACM) and a DNS name from Route 53.

Work through the sections top-to-bottom. Each section explains what the
parameter controls, why it matters, and what value to choose.

---

## 1. Domain name

**What it is:** The public hostname clients will use (e.g. `api.example.com`).

**Choices:**
- **Existing domain** — you already own `example.com` at another registrar
  or in Route 53. You will create a subdomain (`api.example.com`) pointed at
  the load balancer.
- **New domain** — register through Route 53 (`aws route53domains
  register-domain`). Registration is not instant; plan for up to a few hours.

**Parameter in the template:** `DomainName` (e.g. `api.example.com`)

---

## 2. Route 53 hosted zone

**What it is:** The authoritative DNS zone for your apex domain
(`example.com`). Route 53 stores records here.

**Key points:**
- One hosted zone per apex domain.
- If you registered via Route 53, the zone is created for you.
- If your registrar is external, create a public hosted zone manually and
  update the registrar's NS records to Route 53's name servers — otherwise
  DNS validation will fail.

**Parameter in the template:** `HostedZoneId` (the `Z...` ID, not the name)

---

## 3. DNS record for the load balancer

**What it is:** The record that sends `api.example.com` to the ALB.

**Choices:**
- **Alias A/AAAA record (recommended)** — Route 53-specific, free, resolves
  directly to the ALB with no extra hop. Use this for apex or subdomain
  names pointing at AWS resources.
- **CNAME record** — standard DNS. Works, but cannot be used at the zone
  apex and costs a lookup.

**Sub-parameters:**
- **TTL** — how long resolvers cache the answer. For alias records, TTL is
  managed by AWS. For CNAMEs, 60–300 seconds is typical during rollout;
  raise to 3600+ once stable.
- **Routing policy** — `Simple` for a single ALB. Use `Weighted`,
  `Latency`, or `Failover` for multi-region or blue/green setups.

**The template uses an alias A record — no TTL or routing policy to set.**

---

## 4. ACM certificate

**What it is:** The X.509 certificate the ALB presents during the TLS
handshake. ACM issues publicly trusted certs at no charge.

**Key parameters:**
- **DomainName** — primary name on the cert (`api.example.com`).
- **SubjectAlternativeNames (SANs)** — extra names the cert covers. Useful
  for `www.api.example.com` or wildcard `*.api.example.com`.
- **ValidationMethod** — `DNS` (recommended, auto-renews) or `EMAIL`
  (manual, fragile). Always choose DNS for automation.
- **Region** — ACM certs are regional. The cert must live in the same
  region as the ALB. CloudFront needs certs in `us-east-1`.

**The template creates an ACM cert with DNS validation.**

---

## 5. DNS validation record

**What it is:** A CNAME ACM asks you to publish so it can prove you control
the domain. ACM generates the name and value; you put them in Route 53.

**In CloudFormation/Terraform,** use the resource that accepts
`DomainValidationOptions` and let it auto-create the Route 53 record. The
template does this with `AWS::CertificateManager::Certificate` +
`ValidationMethod: DNS` + `DomainValidationOptions.HostedZoneId`.

---

## 6. Load balancer

### 6a. Type

- **Application Load Balancer (ALB)** — layer 7, HTTP/HTTPS, path and
  host-based routing. **Pick this for TLS termination of web traffic.**
- **Network Load Balancer (NLB)** — layer 4, TCP/UDP/TLS. Use for non-HTTP
  or very high throughput.
- **Gateway Load Balancer** — for inline network appliances. Not relevant
  here.
- **Classic Load Balancer** — legacy, do not use for new builds.

**The template deploys an ALB.**

### 6b. Scheme and network

- **Scheme:**
  - `internet-facing` — public IPs, reachable from the internet.
  - `internal` — private IPs only, reachable from inside the VPC.
- **VPC** — which VPC the ALB lives in.
- **Subnets** — at least two subnets in different AZs. Must be public
  subnets for internet-facing, private for internal.
- **Security group** — controls who can reach the ALB. For
  internet-facing HTTPS, allow `0.0.0.0/0` on port 443 (and optionally 80
  for redirect).

**Parameters in the template:** `VpcId`, `SubnetIds`, `Scheme`

### 6c. Listener protocol

- **Port 443, protocol HTTPS** — the TLS termination listener.
- **Port 80, protocol HTTP** — optional, usually configured to redirect to
  443.
- **SSL policy** — the cipher suite set. Use
  `ELBSecurityPolicy-TLS13-1-2-2021-06` (TLS 1.2+1.3, modern ciphers) unless
  you have a specific compatibility need.

**The template creates a 443 HTTPS listener and a 80→443 redirect.**

### 6d. Target group

**What it is:** The pool of backends the ALB forwards requests to.

**Key parameters:**
- **TargetType:** `instance`, `ip`, `lambda`, or `alb`. `instance` is most
  common for EC2.
- **Protocol / Port:** traffic from ALB to target. Typically `HTTP` on the
  app's port (e.g. 8080). The ALB terminates TLS; the hop to the target
  does not need to be re-encrypted unless you require end-to-end TLS.
- **Health check:** path, protocol, interval, healthy/unhealthy thresholds.
  A wrong path here is the #1 cause of 502s.
- **VPC:** must match the ALB's VPC.

**Parameters in the template:** `TargetPort`, `HealthCheckPath`

### 6e. Certificate

Attach the ACM cert ARN from step 4 to the HTTPS listener. The template
wires this automatically.

---

## 7. Final DNS record

After the ALB exists, create the alias A record from step 3 pointing at the
ALB's DNS name. The template does this in one shot.

---

## Deployment checklist

1. Decide `DomainName` (e.g. `api.example.com`).
2. Find your `HostedZoneId` (`aws route53 list-hosted-zones`).
3. Pick a `VpcId` and two `SubnetIds` in different AZs.
4. Run the deploy command in `README.md`.
5. After `CREATE_COMPLETE`, hit `https://<DomainName>/` — you should see
   the ALB's default 503 (no targets yet) over a valid TLS connection.
6. Register targets (EC2, ECS, etc.) in the output target group ARN.
