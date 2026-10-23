# AWS deployment — ECS Express Mode

This folder deploys Hybrid Medical Agent to Amazon ECS Express Mode using a single container image, an HTTPS endpoint, encrypted SSM parameters for credentials, and EFS for durable local SQLite/document storage.

## Important safety note

The web API currently has no user authentication or per-user access control. ECS Express Mode makes the UI publicly reachable over HTTPS by default. Use synthetic/demo documents only. Do not upload real patient records or protected health information until authentication, authorization, audit logging, and a suitable compliance review are in place.

## What the deployment creates

- An ECR repository and Docker image.
- An ECS Fargate service managed by ECS Express Mode, with HTTPS and a generated AWS domain.
- Encrypted AWS Systems Manager Parameter Store values for the Gemini and Qdrant credentials.
- An EFS file system mounted at `/data` for the local SQLite cache and uploaded source documents.
- CloudWatch logs with a 14-day retention period.
- IAM task-execution and ECS Express infrastructure roles.

The deployment script defaults to the Mumbai region (`ap-south-1`). Override it by setting `AWS_REGION` before running the script.

## Prerequisites

1. An AWS account with permission to create IAM roles and policies, ECR repositories, ECS services, EFS resources and mount targets, EC2 security groups, SSM parameters, and CloudWatch log groups.
2. AWS CLI v2 with credentials configured (AWS IAM Identity Center/SSO is recommended when available).
3. Docker running on your computer, plus `jq`.
4. A default VPC with available public subnets in at least two Availability Zones in the selected region.
5. A Google Gemini API key and Qdrant Cloud URL/API key.

On WSL Ubuntu, install `jq` with `sudo apt-get update && sudo apt-get install -y jq`. Install Docker Desktop and enable WSL integration, and install the current AWS CLI v2.

Configure AWS access with `aws configure` or `aws configure sso`, then verify it using `aws sts get-caller-identity`.

## Deploy

Run the following from the repository root on the `aws-express-deploy` branch:

```bash
bash deploy/aws/deploy.sh
```

The script prompts for credentials without echoing the API keys. It stores them as SecureString parameters in SSM Parameter Store; they are not written into the repository. It then builds and pushes the image, creates the persistent EFS mount, registers an ECS task definition, and provisions the Express Mode service.

When deployment is complete, open:

- `https://hybrid-medical-agent.ecs.ap-south-1.on.aws/`
- `https://hybrid-medical-agent.ecs.ap-south-1.on.aws/api/health`

If you selected another region, replace `ap-south-1` with that region in the URLs. The ECS console also displays the real endpoint under the service's resources.

## Embedding model migration

The deployment uses `gemini-embedding-001` with 768-dimensional outputs and a separate collection named `medical_documents_gemini001`. This avoids mixing vectors from the retired `text-embedding-004` model with the current model. Documents stored in an older Qdrant collection are not automatically copied; re-upload/re-ingest the documents you need in this deployment.

## Troubleshooting

- **AWS CLI doesn't recognize `create-express-gateway-service`:** update to the current AWS CLI v2.
- **Role assumption/permissions error:** confirm your AWS identity can create and pass the required roles and that the account has no organizational policy blocking ECS, EFS, IAM, or load-balancer provisioning.
- **No default VPC or fewer than two Availability Zones:** create/restore a default VPC with public subnets in at least two zones, or adjust the script for your network.
- **Health checks fail:** inspect the `/ecs/hybrid-medical-agent` log group in CloudWatch. Check that Gemini/Qdrant credentials are valid and that the container can reach those external services.
- **Gemini embedding errors:** check Google AI API access and quota. The current model is set explicitly by the task definition.

## Costs and cleanup

ECS Express Mode itself has no extra service fee, but the underlying resources are billable: Fargate compute, an Application Load Balancer, EFS storage and throughput, CloudWatch logs, ECR storage, and network transfer. Check current rates in the [AWS Fargate pricing page](https://aws.amazon.com/fargate/pricing/) and [ECS pricing page](https://aws.amazon.com/ecs/pricing/) before deployment.

To stop the running application, delete the Express Mode service in the Amazon ECS console (or use `aws ecs delete-express-gateway-service --service-arn <service-arn> --monitor-resources`). Then remove the EFS file system and mount targets, SSM parameters under `/hybrid-medical-agent/`, ECR repository, and deployment-specific IAM roles if you no longer need them. Deleting the ECS service alone does not delete these separately created data and credential resources.
