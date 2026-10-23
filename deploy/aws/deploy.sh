#!/usr/bin/env bash
# One-time AWS deployment for Hybrid Medical Agent on ECS Express Mode.
# Run from the repository root in WSL/Linux/macOS with Docker, AWS CLI v2, and jq.
set -Eeuo pipefail

APP_NAME="hybrid-medical-agent"
REPO_NAME="hybrid-medical-agent"
PARAMETER_PREFIX="/hybrid-medical-agent"
AWS_REGION="${AWS_REGION:-ap-south-1}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

for cmd in aws docker jq; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "Missing dependency: $cmd" >&2
    exit 1
  }
done

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "AWS CLI is not authenticated. Run 'aws configure' or configure an AWS IAM Identity Center profile first." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
EXEC_ROLE="ecsTaskExecutionRole"
INFRA_ROLE="ecsInfrastructureRoleForExpressServices"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  read -r -s -p "Gemini API key: " GEMINI_API_KEY
  echo
fi
if [[ -z "${QDRANT_URL:-}" ]]; then
  read -r -p "Qdrant Cloud URL: " QDRANT_URL
fi
if [[ -z "${QDRANT_API_KEY:-}" ]]; then
  read -r -s -p "Qdrant API key: " QDRANT_API_KEY
  echo
fi
[[ -n "$GEMINI_API_KEY" && -n "$QDRANT_URL" && -n "$QDRANT_API_KEY" ]] || {
  echo "Gemini API key, Qdrant URL, and Qdrant API key are required for cloud-backed answers." >&2
  exit 1
}

echo "Deploying $APP_NAME to AWS region $AWS_REGION (account $ACCOUNT_ID)."

# ECS Express Mode needs the standard task execution role and infrastructure role.
aws iam get-role --role-name "$EXEC_ROLE" >/dev/null 2>&1 || \
  aws iam create-role --role-name "$EXEC_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null

aws iam get-role --role-name "$INFRA_ROLE" >/dev/null 2>&1 || \
  aws iam create-role --role-name "$INFRA_ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null

aws iam attach-role-policy --role-name "$EXEC_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam attach-role-policy --role-name "$INFRA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices

# Allow ECS to fetch the three encrypted SSM parameters as the task starts.
aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name HybridMedicalAgentReadParameters \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameters\"],\"Resource\":\"arn:aws:ssm:$AWS_REGION:$ACCOUNT_ID:parameter/hybrid-medical-agent/*\"}]}" >/dev/null

VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
  echo "No default VPC was found in $AWS_REGION. Create a default VPC or adapt the deployment network settings first." >&2
  exit 1
fi
VPC_CIDR="$(aws ec2 describe-vpcs --vpc-ids "$VPC_ID" --query 'Vpcs[0].CidrBlock' --output text)"
SUBNETS_JSON="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=state,Values=available" --output json)"
mapfile -t SUBNET_IDS < <(printf '%s' "$SUBNETS_JSON" | jq -r '[.Subnets | sort_by(.AvailabilityZone) | group_by(.AvailabilityZone)[] | .[0].SubnetId] | .[]')
if [[ "${#SUBNET_IDS[@]}" -lt 2 ]]; then
  echo "ECS Express Mode needs public subnets in at least two Availability Zones in the default VPC." >&2
  exit 1
fi

# Store credentials as encrypted SecureString parameters; no keys are committed to Git.
aws ssm put-parameter --name "$PARAMETER_PREFIX/GEMINI_API_KEY" --type SecureString --value "$GEMINI_API_KEY" --overwrite >/dev/null
aws ssm put-parameter --name "$PARAMETER_PREFIX/QDRANT_URL" --type SecureString --value "$QDRANT_URL" --overwrite >/dev/null
aws ssm put-parameter --name "$PARAMETER_PREFIX/QDRANT_API_KEY" --type SecureString --value "$QDRANT_API_KEY" --overwrite >/dev/null
unset GEMINI_API_KEY QDRANT_API_KEY

# Create/reuse the encrypted EFS volume for SQLite and uploaded source documents.
EFS_SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=hybrid-medical-agent-efs" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ -z "$EFS_SG_ID" || "$EFS_SG_ID" == "None" ]]; then
  EFS_SG_ID="$(aws ec2 create-security-group --group-name hybrid-medical-agent-efs \
    --description "Private NFS access for Hybrid Medical Agent EFS" --vpc-id "$VPC_ID" \
    --query GroupId --output text)"
fi
aws ec2 authorize-security-group-ingress --group-id "$EFS_SG_ID" \
  --ip-permissions "[{\"IpProtocol\":\"tcp\",\"FromPort\":2049,\"ToPort\":2049,\"IpRanges\":[{\"CidrIp\":\"$VPC_CIDR\",\"Description\":\"NFS within the VPC only\"}]}]" >/dev/null 2>&1 || true

FS_ID="$(aws efs create-file-system --creation-token hybrid-medical-agent-data \
  --encrypted --performance-mode generalPurpose --throughput-mode bursting \
  --tags Key=Name,Value=hybrid-medical-agent-data --query FileSystemId --output text)"
aws efs wait file-system-available --file-system-id "$FS_ID"

EXISTING_MOUNTS="$(aws efs describe-mount-targets --file-system-id "$FS_ID" --query 'MountTargets[].SubnetId' --output text 2>/dev/null || true)"
for subnet_id in "${SUBNET_IDS[@]}"; do
  if ! grep -qw "$subnet_id" <<< "$EXISTING_MOUNTS"; then
    aws efs create-mount-target --file-system-id "$FS_ID" --subnet-id "$subnet_id" \
      --security-groups "$EFS_SG_ID" >/dev/null
  fi
done
aws efs wait mount-target-available --file-system-id "$FS_ID"

AP_ID="$(aws efs describe-access-points --file-system-id "$FS_ID" --output json | \
  jq -r '.AccessPoints[]? | select(.Name=="hybrid-medical-agent-access") | .AccessPointId' | head -n 1)"
if [[ -z "$AP_ID" ]]; then
  AP_ID="$(aws efs create-access-point --file-system-id "$FS_ID" \
    --posix-user Uid=10001,Gid=10001 \
    --root-directory "Path=/hybrid-medical-agent,CreationInfo={OwnerUid=10001,OwnerGid=10001,Permissions=750}" \
    --tags Key=Name,Value=hybrid-medical-agent-access --query AccessPointId --output text)"
fi

# Build and publish the image to ECR.
aws ecr describe-repositories --repository-names "$REPO_NAME" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$REPO_NAME" \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability MUTABLE >/dev/null
ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
docker build --platform linux/amd64 -t "$REPO_NAME:$IMAGE_TAG" .
docker tag "$REPO_NAME:$IMAGE_TAG" "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:$IMAGE_TAG"

LOG_GROUP="/ecs/$APP_NAME"
aws logs create-log-group --log-group-name "$LOG_GROUP" >/dev/null 2>&1 || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14 >/dev/null

EXEC_ROLE_ARN="$(aws iam get-role --role-name "$EXEC_ROLE" --query 'Role.Arn' --output text)"
TASK_DEF_JSON="$(jq -n \
  --arg image "$ECR_URI:$IMAGE_TAG" \
  --arg execRole "$EXEC_ROLE_ARN" \
  --arg region "$AWS_REGION" \
  --arg account "$ACCOUNT_ID" \
  --arg fileSystem "$FS_ID" \
  --arg accessPoint "$AP_ID" \
  --arg logGroup "$LOG_GROUP" '
  {
    family: "hybrid-medical-agent",
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: "1024",
    memory: "2048",
    executionRoleArn: $execRole,
    containerDefinitions: [{
      name: "Main",
      image: $image,
      essential: true,
      portMappings: [{containerPort: 7860, protocol: "tcp", name: "http"}],
      environment: [
        {name:"GEMINI_MODEL",value:"gemini-2.5-flash"},
        {name:"GEMINI_EMBEDDING_MODEL",value:"gemini-embedding-001"},
        {name:"GEMINI_EMBEDDING_DIM",value:"768"},
        {name:"QDRANT_COLLECTION",value:"medical_documents_gemini001"},
        {name:"CLOUD_MCP_HOST",value:"127.0.0.1"},
        {name:"CLOUD_MCP_PORT",value:"8000"},
        {name:"CLOUD_SERVER_URL",value:"http://127.0.0.1:8000/mcp"},
        {name:"CLOUD_TIMEOUT_SECONDS",value:"20"},
        {name:"UI_HOST",value:"0.0.0.0"},
        {name:"UI_PORT",value:"7860"},
        {name:"LOCAL_DB_PATH",value:"/data/local_cache.db"}
      ],
      secrets: [
        {name:"GEMINI_API_KEY",valueFrom:("arn:aws:ssm:"+$region+":"+$account+":parameter/hybrid-medical-agent/GEMINI_API_KEY")},
        {name:"QDRANT_URL",valueFrom:("arn:aws:ssm:"+$region+":"+$account+":parameter/hybrid-medical-agent/QDRANT_URL")},
        {name:"QDRANT_API_KEY",valueFrom:("arn:aws:ssm:"+$region+":"+$account+":parameter/hybrid-medical-agent/QDRANT_API_KEY")}
      ],
      mountPoints: [{sourceVolume:"medical-agent-data",containerPath:"/data",readOnly:false}],
      healthCheck: {
        command:["CMD-SHELL","python -c \"import urllib.request; urllib.request.urlopen(\u0027http://127.0.0.1:7860/api/health\u0027, timeout=5).read()\""],
        interval:30, timeout:8, retries:3, startPeriod:60
      },
      logConfiguration: {
        logDriver:"awslogs",
        options: {
          "awslogs-group":$logGroup,
          "awslogs-region":$region,
          "awslogs-stream-prefix":"ecs"
        }
      }
    }],
    volumes: [{
      name:"medical-agent-data",
      efsVolumeConfiguration: {
        fileSystemId:$fileSystem,
        rootDirectory:"/",
        transitEncryption:"ENABLED",
        authorizationConfig:{accessPointId:$accessPoint,iam:"DISABLED"}
      }
    }]
  }')"

TASK_DEF_ARN="$(aws ecs register-task-definition --cli-input-json "$TASK_DEF_JSON" --query 'taskDefinition.taskDefinitionArn' --output text)"
INFRA_ROLE_ARN="$(aws iam get-role --role-name "$INFRA_ROLE" --query 'Role.Arn' --output text)"

if aws ecs describe-clusters --clusters default --query 'clusters[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
  :
else
  aws ecs create-cluster --cluster-name default >/dev/null
fi

echo "Creating ECS Express Mode service; AWS will provision the HTTPS load balancer."
aws ecs create-express-gateway-service \
  --service-name "$APP_NAME" \
  --task-definition-arn "$TASK_DEF_ARN" \
  --infrastructure-role-arn "$INFRA_ROLE_ARN" \
  --health-check-path "/api/health" \
  --scaling-target '{"minTaskCount":1,"maxTaskCount":1}' \
  --monitor-resources DEPLOYMENT \\
  --monitor-mode TEXT-ONLY

echo
echo "Deployment request completed."
echo "Try: https://$APP_NAME.ecs.$AWS_REGION.on.aws/"
echo "Health check: https://$APP_NAME.ecs.$AWS_REGION.on.aws/api/health"
echo "EFS data is persistent at: $FS_ID (mounted at /data inside the container)."
echo "Keep this service demo-only until authentication/authorization is added; do not upload real patient data."
