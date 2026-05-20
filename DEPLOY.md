# Deployment runbook — AWS EKS

End-to-end steps to build the container, publish to ECR, provision an EKS cluster, and ship the Streamlit app behind an ALB with TLS.

Substitute these placeholders throughout:

| Placeholder | Example | Where to find / decide |
| --- | --- | --- |
| `ACCOUNT_ID` | `123456789012` | `aws sts get-caller-identity --query Account --output text` |
| `REGION` | `us-east-1` | Pick one close to your users; keep it the same in every step below. |
| `CLUSTER_NAME` | `uncia-sense` | Anything you like. |
| `REPO` | `uncia-sense` | ECR repository name. |
| `TAG` | `v1` | Semantic image tag — bump on every push. |
| `YOUR_DOMAIN` | `uncia-sense.example.com` | Hostname you'll route to the ALB. |
| `CERT_ARN` | `arn:aws:acm:us-east-1:123…:certificate/abc-uuid` | ACM cert ARN — see §7. |

---

## 0. Prerequisites

Install once on your workstation:

```powershell
choco install awscli kubernetes-cli eksctl docker-desktop kubernetes-helm
aws configure        # set up Access Key + default region
docker --version     # sanity check
```

Verify your AWS identity:

```powershell
aws sts get-caller-identity
```

---

## 0.5. Secret-scanning hooks (one-time per clone)

Before building anything, wire up the pre-commit hook so a stray AWS key or `sk-ant-…` token can't leave your laptop. This is the only layer that catches secrets *before* they hit a remote.

```powershell
pip install pre-commit
pre-commit install        # writes .git/hooks/pre-commit in this clone
pre-commit run --all-files   # baseline scan of the entire repo
```

After this, every `git commit` runs [gitleaks](https://github.com/gitleaks/gitleaks) against the staged diff. A detection aborts the commit and prints the offending file/line — fix or unstage, then retry.

The hook config lives in [.pre-commit-config.yaml](.pre-commit-config.yaml). It catches AWS access keys (`AKIA…` / `ASIA…`), AWS secret access keys, Anthropic / DeepSeek API keys, GitHub tokens, generic high-entropy strings, and JWTs out of the box — no custom rules required.

**CI backstop.** [.github/workflows/secrets.yml](.github/workflows/secrets.yml) runs the same gitleaks scan on every push and PR. Wire it into your branch-protection rule (Settings → Branches → Protect main → Require `gitleaks` status check) so a developer who skipped `pre-commit install` can't merge a leaked secret.

**GitHub-side push protection (private repos with Advanced Security):** Settings → Code security → enable *Secret scanning* and *Push protection*. This blocks the `git push` itself when GitHub's own scanner spots a known-pattern secret — strongest defense, no code changes.

**If a key leaks anyway**, the order is **rotate first, scrub second**: revoke the credential at the provider (AWS IAM / Anthropic console / DeepSeek console), then remove it from history with `git filter-repo --replace-text` and force-push. Old clones still contain the secret — anyone who cloned before the scrub must re-clone.

---

## 1. Build & smoke-test locally

```powershell
docker build -t uncia-sense:dev .

# Smoke test — pass your local key through. The container expects
# ANTHROPIC_API_KEY (and optionally DEEPSEEK_API_KEY) as env vars.
docker run --rm -p 8501:8501 `
    -e ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY `
    -e DEEPSEEK_API_KEY=$env:DEEPSEEK_API_KEY `
    uncia-sense:dev
```

Open <http://localhost:8501>, upload a sample submission, confirm the analysis completes. Press `Ctrl-C` to stop the container.

**Image size sanity:** `docker image ls uncia-sense:dev` should report ~400–500 MB. Tesseract + the Python wheels are the bulk.

---

## 2. Push to ECR

```powershell
$ACCOUNT = aws sts get-caller-identity --query Account --output text
$REGION  = "us-east-1"
$REPO    = "uncia-sense"
$TAG     = "v1"
$REGISTRY = "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

# Create the repo once. Idempotent — ignore "already exists" errors on reruns.
aws ecr create-repository --repository-name $REPO --region $REGION `
    --image-scanning-configuration scanOnPush=true

# Authenticate Docker to ECR
aws ecr get-login-password --region $REGION | `
    docker login --username AWS --password-stdin $REGISTRY

# Tag and push
docker tag uncia-sense:dev "${REGISTRY}/${REPO}:${TAG}"
docker push "${REGISTRY}/${REPO}:${TAG}"
```

Confirm the image landed:

```powershell
aws ecr describe-images --repository-name $REPO --region $REGION
```

---

## 3. Provision the EKS cluster

```powershell
eksctl create cluster `
    --name $CLUSTER_NAME `
    --region $REGION `
    --version 1.30 `
    --nodegroup-name workers `
    --node-type t3.medium `
    --nodes 2 --nodes-min 1 --nodes-max 3 `
    --managed `
    --with-oidc
```

Takes **15–20 minutes**. `--with-oidc` is mandatory for the next step (IAM Roles for Service Accounts). When it finishes:

```powershell
kubectl get nodes      # expect two Ready nodes
```

`eksctl` auto-updates your kubeconfig. If you ever need to refresh it:

```powershell
aws eks update-kubeconfig --name $CLUSTER_NAME --region $REGION
```

---

## 4. Install the AWS Load Balancer Controller

The Ingress in this repo provisions an ALB via this controller. Without it, the Ingress will hang in `<pending>` forever.

```powershell
# 4a. IAM policy that lets the controller call AWS APIs
curl https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.2/docs/install/iam_policy.json -o iam_policy.json

aws iam create-policy `
    --policy-name AWSLoadBalancerControllerIAMPolicy `
    --policy-document file://iam_policy.json

# 4b. Bind the policy to a K8s ServiceAccount via IRSA
eksctl create iamserviceaccount `
    --cluster=$CLUSTER_NAME `
    --namespace=kube-system `
    --name=aws-load-balancer-controller `
    --attach-policy-arn=arn:aws:iam::${ACCOUNT}:policy/AWSLoadBalancerControllerIAMPolicy `
    --override-existing-serviceaccounts --approve

# 4c. Install via Helm
helm repo add eks https://aws.github.io/eks-charts
helm repo update
helm install aws-load-balancer-controller eks/aws-load-balancer-controller `
    -n kube-system `
    --set clusterName=$CLUSTER_NAME `
    --set serviceAccount.create=false `
    --set serviceAccount.name=aws-load-balancer-controller

# Verify
kubectl -n kube-system get deployment aws-load-balancer-controller
```

---

## 5. Create the API-key Secret

**Preferred — never touches disk:**

```powershell
kubectl create secret generic uncia-sense-secrets `
    --from-literal=ANTHROPIC_API_KEY=$env:ANTHROPIC_API_KEY `
    --from-literal=DEEPSEEK_API_KEY=$env:DEEPSEEK_API_KEY
```

Alternative if you prefer a manifest: copy `k8s/secret.example.yaml` → `k8s/secret.yaml`, fill in the values, add `k8s/secret.yaml` to `.gitignore`, then `kubectl apply -f k8s/secret.yaml`. **Never commit the filled-in file.**

---

## 6. Patch the manifests & apply

The repo's manifests contain three concrete placeholders that you must edit before the first apply:

| File | Placeholder | Replace with |
| --- | --- | --- |
| `k8s/deployment.yaml` | `ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/uncia-sense:v1` | The full ECR image URI you pushed in §2. |
| `k8s/ingress.yaml` | `arn:aws:acm:REGION:ACCOUNT_ID:certificate/CERT_UUID` | Your ACM certificate ARN (provision in §7 if you don't have one). |
| `k8s/ingress.yaml` | `YOUR_DOMAIN_HERE` | The hostname you'll point at the ALB. |

Then:

```powershell
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml

# Watch the pod come up — should reach 1/1 Ready in ~30s.
kubectl get pods -w

# Grab the ALB hostname (takes 1–3 minutes to appear in ADDRESS).
kubectl get ingress uncia-sense
```

---

## 7. DNS + TLS

1. **Request an ACM certificate** for `YOUR_DOMAIN`:

   ```powershell
   aws acm request-certificate `
       --domain-name $YOUR_DOMAIN `
       --validation-method DNS `
       --region $REGION
   ```

   Complete the CNAME validation record (ACM prints the values you need). Once the cert shows `Status: ISSUED`, copy the ARN into `k8s/ingress.yaml` and `kubectl apply -f k8s/ingress.yaml` again.

2. **Route 53 alias**: in your hosted zone, create an `A` record with `Alias: Yes` pointing at the ALB hostname from `kubectl get ingress`. Propagation is typically <60s.

3. **Verify**:

   ```powershell
   curl -I https://$YOUR_DOMAIN/_stcore/health
   ```

   Expect `200 OK`.

---

## Updating (subsequent releases)

```powershell
$TAG = "v2"        # bump

docker build -t "uncia-sense:$TAG" .
docker tag "uncia-sense:$TAG" "${REGISTRY}/${REPO}:${TAG}"
docker push "${REGISTRY}/${REPO}:${TAG}"

# Triggers a rolling restart with the new image
kubectl set image deployment/uncia-sense app="${REGISTRY}/${REPO}:${TAG}"
kubectl rollout status deployment/uncia-sense

# Rollback if something is wrong
kubectl rollout undo deployment/uncia-sense
```

---

## Caveats specific to this app

- **No built-in auth.** A public Ingress means anyone with the URL can run analyses against your Anthropic / DeepSeek bill. For anything beyond a short internal demo, either set `scheme: internal` on the Ingress and put it behind a VPN, or front the ALB with Cognito (`alb.ingress.kubernetes.io/auth-type: cognito`). Don't skip this.
- **Pin to 1 replica** until you've verified sticky sessions end-to-end. Streamlit's `st.session_state` is in-process; without stickiness, every WebSocket reconnect can land on a different pod and reset the user's session mid-analysis. The Ingress manifest already enables `lb_cookie` stickiness — you'll need to load-test before raising `replicas`.
- **Long requests need a long ALB idle timeout.** Already set to 180s in the Ingress. If you change the `Full` prompt to something even slower, raise it.
- **Memory.** The Deployment requests 512 Mi / limits 1 Gi. Match that to `MAX_TOTAL_UPLOAD_MB` in `config.py` (current default 50 MB → ~150 MB peak working set is comfortable inside 1 Gi).
- **Tesseract image weight.** ~150 MB on top of `python:3.12-slim`, total image ~400–500 MB. First pull on a fresh node takes 30–60s; subsequent pulls are cached.
- **API cost rate.** ~$0.20–0.50 per analysis on Opus. Add a WAF rule rate-limiting by source IP if the Ingress is internet-facing.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Ingress `ADDRESS` stays empty for >5 min | ALB controller not installed, or its ServiceAccount lacks IAM permissions | Re-check §4; `kubectl -n kube-system logs deploy/aws-load-balancer-controller` |
| Pod stuck in `ImagePullBackOff` | ECR auth not configured for the node group | The eksctl managed node group attaches the right role automatically; if you used a custom role, attach `AmazonEC2ContainerRegistryReadOnly` |
| App reports "ANTHROPIC_API_KEY not set" | Secret not bound to the pod | `kubectl describe pod -l app=uncia-sense` — look for `envFrom` resolution errors; recreate the Secret |
| WebSocket disconnects ~60s into an analysis | ALB idle timeout still at 60s default | Re-apply Ingress; verify `kubectl describe ingress uncia-sense \| grep idle_timeout` shows 180 |
| Analysis fails with `Streamlit … session expired` after a load-balancer-attached reload | Sticky sessions disabled or scaled to >1 without verifying | Confirm the `target-group-attributes` annotation is present; drop back to `replicas: 1` |
| `kubectl get pods` shows `OOMKilled` | Memory limit too tight for a large submission | Raise `resources.limits.memory` in `k8s/deployment.yaml` and/or lower `MAX_TOTAL_UPLOAD_MB` in `config.py` |

---

## Teardown

```powershell
kubectl delete -f k8s/ingress.yaml          # deletes the ALB
kubectl delete -f k8s/service.yaml
kubectl delete -f k8s/deployment.yaml
kubectl delete secret uncia-sense-secrets

helm uninstall aws-load-balancer-controller -n kube-system

eksctl delete cluster --name $CLUSTER_NAME --region $REGION   # ~10 minutes

aws ecr delete-repository --repository-name $REPO --region $REGION --force
```

ACM certs and Route 53 records are not deleted by the above — clean them up manually if you're done.
