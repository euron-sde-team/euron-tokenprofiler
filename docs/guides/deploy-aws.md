# Deploying to AWS

> The AWS environments (`aws-dev`, `aws-prod`) pass `terraform validate` and the same modules run against Floci locally.
> They have **not been applied** to a real account. Review the plan and costs before applying.

## What it builds

A VPC with public and private subnets, an ALB, one EC2 instance in an Auto Scaling group of size one (IMDSv2, SSM, no SSH),
an ECR repository, an encrypted reports bucket with public access blocked, a Secrets Manager secret holding the pepper
and bootstrap key, CloudWatch alarms and an SNS topic. The host backs up SQLite to S3 daily and restores on boot.

## Steps

```bash
cd infra/terraform/envs/aws-dev
cp backend.tf.example backend.tf          # remote state (an S3 bucket you own)
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform plan
terraform apply
```

Push the API image to the ECR repository from the outputs, then let the instance pull it. Add an HTTPS listener with an
ACM certificate before exposing it; `aws-prod` expects one.

## Cost and shape

One small instance, an ALB and a NAT gateway dominate the bill. A size-one group means a replacement takes a few minutes
(restore from S3), not zero downtime. See [ADR-0001](../architecture/adr/0001-single-process-sqlite.md).
