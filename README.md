# Best Explanation of the project 

A production-grade, cloud-native Python Flask application deployed on AWS using ECS Fargate, with infrastructure managed entirely by Terraform and a fully automated CI/CD pipeline via GitHub Actions.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Secret Management](#secret-management)
- [Infrastructure Setup](#infrastructure-setup)
- [CI/CD Pipeline](#cicd-pipeline)
- [Local Development](#local-development)
- [Challenges & Resolutions](#challenges--resolutions)

---

## Architecture Overview

```
Internet
   │
   ▼
┌─────────────────────────────────────────────────┐
│              Application Load Balancer           │
│           (Public Subnets: AZ-a, AZ-b)          │
└────────────────────┬────────────────────────────┘
                     │
         ┌───────────▼───────────┐
         │     ECS Fargate       │
         │  (Private Subnets)    │
         │  Task: tksde    │ ◄── Secrets Manager
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │   RDS PostgreSQL 16   │
         │   (Private Subnets)   │
         │     Multi-AZ          │
         └───────────────────────┘
```

### VPC Design

| Component | Details |
|---|---|
| VPC CIDR | `10.0.0.0/16` |
| Public Subnets | `10.0.1.0/24`, `10.0.2.0/24` (us-east-1a, us-east-1b) |
| Private Subnets | `10.0.3.0/24`, `10.0.4.0/24` (us-east-1a, us-east-1b) |
| Internet Gateway | Attached to VPC, routes traffic for public subnets |
| NAT Gateways | One per AZ, in public subnets — provides outbound internet for private resources |
| Route Tables | Separate public and private route tables |

The ALB sits in the public subnets and accepts inbound HTTP/HTTPS traffic. ECS tasks and the RDS instance live exclusively in private subnets and have no direct internet exposure. NAT Gateways allow private resources to pull images and make outbound API calls without being publicly accessible.

### ECS Fargate

The application runs as a containerized Fargate service. No EC2 instances are provisioned or managed. The service maintains `2` desired tasks across two AZs for high availability. A deployment circuit breaker is enabled — if a new deployment is unhealthy, ECS automatically rolls back to the previous stable task definition.

### RDS PostgreSQL

PostgreSQL 16 is deployed in a Multi-AZ configuration in private subnets. Storage encryption is enabled at rest. Automated backups are retained for 7 days. Deletion protection is enabled to prevent accidental data loss. The RDS instance is never publicly accessible.

---

## Project Structure

```
tksde/
├── app.py                          # Flask application
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Multi-stage container build
├── .dockerignore
├── tests/
│   └── test_app.py                 # pytest unit tests
├── terraform/
│   ├── providers.tf                # Terraform version, providers, S3 backend
│   ├── variables.tf                # All input variables
│   ├── main.tf                     # All infrastructure resources
│   └── outputs.tf                  # Output values
└── .github/
    └── workflows/
        └── deploy.yml              # CI/CD pipeline
```

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Terraform | >= 1.7.0 | Infrastructure provisioning |
| AWS CLI | >= 2.x | AWS authentication and interaction |
| Docker | >= 24.x | Building container images |
| Python | >= 3.12 | Local development and testing |
| GitHub Actions | — | CI/CD orchestration |

You will need an AWS account with permissions to manage: VPC, EC2 (ALB, Security Groups, NAT), RDS, ECS, ECR, IAM, Secrets Manager, S3, DynamoDB, and CloudWatch.

---

## Secret Management

This project uses a three-phase secret management strategy that ensures no plaintext credentials ever appear in code, environment files, or CI/CD logs.

### Phase 1 — Terraform Generates the Secret

`random_password` generates a cryptographically strong 24-character password at infrastructure creation time. This value only exists in Terraform state (stored encrypted in S3) and is never written to any file.

### Phase 2 — Secrets Manager Stores It

`aws_secretsmanager_secret_version` stores a JSON object containing the full database connection parameters:

```json
{
  "username": "tksde_user",
  "password": "<generated>",
  "dbname":   "tksde",
  "host":     "<rds-endpoint>",
  "port":     5432
}
```

### Phase 3 — ECS Injects Credentials at Runtime

The ECS task definition uses the `secrets` field (not `environment`) to pull individual JSON keys from the Secrets Manager ARN at container startup. The ECS Task Execution Role is granted `secretsmanager:GetSecretValue` via a scoped IAM policy. The running container receives the values as standard environment variables (`DB_HOST`, `DB_USER`, `DB_PASS`, `DB_NAME`) without any secret ever being embedded in the task definition JSON, container image, or CI/CD pipeline.

**Why this matters:** The attack surface for credential theft is reduced to zero at the application layer. Even if someone extracts the ECR image or reads the task definition, they find no credentials.

---

## Infrastructure Setup

### Step 1 — Bootstrap the Terraform State Backend

Before the first `terraform init`, create the S3 bucket and DynamoDB table for state management manually (this is a one-time operation):

```bash
aws s3api create-bucket \
  --bucket tksde-tf-state \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket tksde-tf-state \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket tksde-tf-state \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws dynamodb create-table \
  --table-name tksde-tf-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### Step 2 — Create ECR Repository

```bash
aws ecr create-repository \
  --repository-name tksde \
  --image-scanning-configuration scanOnPush=true \
  --region us-east-1
```

Note the ECR URI (format: `<account_id>.dkr.ecr.us-east-1.amazonaws.com/tksde`). You will need it in Step 4.

### Step 3 — Initialize and Apply Terraform

```bash
cd terraform
terraform init
terraform plan -var="ecr_image_uri=<ECR_URI>:latest"
terraform apply -var="ecr_image_uri=<ECR_URI>:latest"
```

Terraform will provision all resources in the correct dependency order. The initial `apply` for an RDS Multi-AZ instance may take 10–15 minutes.

### Step 4 — Push the Initial Image

```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ECR_URI>

docker build -t tksde:latest .
docker tag tksde:latest <ECR_URI>:latest
docker push <ECR_URI>:latest
```

### Step 5 — Configure GitHub Actions Secrets

In your GitHub repository, go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `AWS_ROLE_ARN` | ARN of the IAM role GitHub Actions will assume (OIDC) |

Create an IAM OIDC identity provider for `token.actions.githubusercontent.com` and attach a role with the following trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:<ORG>/<REPO>:ref:refs/heads/main"
      }
    }
  }]
}
```

The role needs permissions for: `ecr:*`, `ecs:UpdateService`, `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, and `iam:PassRole`.

---

## CI/CD Pipeline

The pipeline defined in `.github/workflows/deploy.yml` runs on every push to `main` and every pull request targeting `main`.

### Pipeline Stages

```
push to main / PR
       │
       ▼
┌─────────────────────┐
│   lint-and-test     │  flake8 + pytest
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    trivy-scan       │  Image vulnerability scan (CRITICAL/HIGH)
└──────────┬──────────┘
           │ (main branch only)
           ▼
┌─────────────────────┐
│  build-and-push     │  Build Docker image → push to ECR (sha + latest tags)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      deploy         │  Force new ECS deployment → wait for service stability
└─────────────────────┘
```

PRs only run `lint-and-test` and `trivy-scan`. The `build-and-push` and `deploy` jobs are gated on `github.ref == 'refs/heads/main'`, preventing accidental deployments from feature branches.

Each image is tagged with the Git commit SHA (`${{ github.sha }}`) providing a complete and immutable deployment audit trail.

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DB_HOST=localhost
export DB_USER=postgres
export DB_PASS=postgres
export DB_NAME=tksde

python app.py
```

Run a local PostgreSQL instance with Docker:

```bash
docker run -d \
  --name tksde-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=tksde \
  -p 5432:5432 \
  postgres:16-alpine
```

Run tests:

```bash
pytest tests/ -v
```

---

## Challenges & Resolutions

### Challenge 1 — Circular Dependency Between RDS and Secrets Manager

**Problem:** The Secrets Manager secret version needed to include the RDS endpoint (`host`), but Terraform could not create the secret version before the RDS instance existed, which would be needed to hydrate the ECS task definition.

**Resolution:** The `aws_secretsmanager_secret_version` resource uses an explicit `depends_on = [aws_db_instance.postgres]` to ensure RDS is fully provisioned before the secret version is written. The ECS task definition references the secret ARN directly, which is known at plan time before the secret's content is written, breaking the circular dependency.

---

### Challenge 2 — ECS Task Pulling Secrets Requires Specific IAM Scope

**Problem:** The managed policy `AmazonECSTaskExecutionRolePolicy` grants access to ECR and CloudWatch Logs, but does not include `secretsmanager:GetSecretValue`. Tasks would fail at startup with an access denied error.

**Resolution:** A separate inline `aws_iam_role_policy` with the `secretsmanager:GetSecretValue` action scoped to the exact secret ARN is attached to the Task Execution Role. This follows the principle of least privilege — the role can only read that one secret.

---

### Challenge 3 — NAT Gateway Not Ready When Private Resources Try to Pull Images

**Problem:** On the very first `terraform apply`, ECS tasks would launch in private subnets before the NAT Gateway's route was fully propagated, resulting in image pull failures from ECR.

**Resolution:** Terraform's dependency graph correctly orders resource creation: NAT Gateways → Private Route Tables → Route Table Associations. The ECS service `depends_on` the ALB listener and execution role. With the `aws ecs wait services-stable` step in the CI/CD pipeline, deployments are validated before the pipeline reports success.

---

### Challenge 4 — GitHub Actions Authentication Without Long-Lived Keys

**Problem:** Storing static AWS access keys as GitHub Secrets is a security anti-pattern. Keys can be leaked through log output or repository forks.

**Resolution:** GitHub Actions OIDC integration is used. The workflow assumes an IAM role via `aws-actions/configure-aws-credentials` with `role-to-assume`. No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` secret exists. Each workflow run receives a short-lived, automatically-rotated token scoped to the specific repository and branch.

---

### Challenge 5 — Trivy Blocking Deployment on Unfixable OS Vulnerabilities

**Problem:** Trivy scanning a base `python:3.12-slim` image frequently reports vulnerabilities in OS packages that have no upstream fix available yet, blocking the pipeline indefinitely.

**Resolution:** The Trivy action is configured with `ignore-unfixed: true`. This ensures only vulnerabilities with a known fix available will fail the build, preventing alert fatigue from unactionable findings while still catching exploitable issues with available patches.
