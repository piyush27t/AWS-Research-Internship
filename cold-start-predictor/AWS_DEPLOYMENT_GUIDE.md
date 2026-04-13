# AWS Deployment Guide: Cold-Start Prediction System

Complete step-by-step instructions for deploying the cold-start prediction system to AWS.

---

## Phase 1: Prerequisites & Setup

### 1.1 AWS Account Setup

```bash
# Install AWS CLI v2
# Windows: https://awscli.amazonaws.com/AWSCLIV2.msi
# Or via PowerShell:
msiexec.exe /i https://awscli.amazonaws.com/AWSCLIV2.msi

# Verify installation
aws --version

# Configure AWS credentials
aws configure
# Enter: AWS Access Key ID, Secret Access Key, Default region (us-east-1), Output format (json)
```

### 1.2 Required IAM Permissions

Create an IAM user with the following policies:

- `AmazonEC2ContainerRegistryFullAccess` (for ECR)
- `AWSLambda_FullAccess`
- `CloudFrontFullAccess`
- `S3FullAccess`
- `EventBridgeFullAccess`
- `IAMFullAccess`
- `ElasticLoadBalancingFullAccess`

### 1.3 Install Required Tools

```bash
# Install Docker Desktop (if deploying API as container)
# Windows: https://www.docker.com/products/docker-desktop

# Install Node.js for dashboard build
# Windows: https://nodejs.org/en/download/

# Verify
docker --version
node --version
npm --version
```

---

## Phase 2: Backend API Deployment (FastAPI on Lambda)

### 2.1 Create S3 Bucket for Model Artifacts

```bash
# Set variables
$BUCKET_NAME = "cold-start-models-$(Get-Random -Minimum 1000 -Maximum 9999)"
$REGION = "us-east-1"

# Create bucket
aws s3api create-bucket `
    --bucket $BUCKET_NAME `
    --region $REGION

# Upload trained models
aws s3 cp ./models/lstm_model.keras s3://$BUCKET_NAME/models/
aws s3 cp ./models/ s3://$BUCKET_NAME/models/ --recursive

# Enable versioning
aws s3api put-bucket-versioning `
    --bucket $BUCKET_NAME `
    --versioning-configuration Status=Enabled

Write-Host "✓ Bucket created: $BUCKET_NAME"
```

### 2.2 Create ECR Repository for Docker Image

```bash
# Set variables
$ECR_REPO_NAME = "cold-start-api"
$AWS_ACCOUNT_ID = (aws sts get-caller-identity --query 'Account' --output text)
$ECR_URI = "$AWS_ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO_NAME"

# Create ECR repository
aws ecr create-repository `
    --repository-name $ECR_REPO_NAME `
    --region $REGION

# Login to ECR
$ecrPassword = (aws ecr get-authorization-token --region $REGION --query 'authorizationData[0].authorizationToken' --output text)
echo $ecrPassword | docker login --username AWS --password-stdin $ECR_URI

Write-Host "✓ ECR repository created: $ECR_REPO_NAME"
```

### 2.3 Containerize the FastAPI Application

Create [Dockerfile](Dockerfile) in project root:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY configs/ ./configs/
COPY src/ ./src/
COPY models/ ./models/

# AWS Lambda handler
RUN pip install mangum

EXPOSE 8000

# Run with Uvicorn (for local testing) or Mangum handler (for Lambda)
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2.4 Build and Push Docker Image

```bash
# Build image
docker build -t $ECR_URI:latest .

# Push to ECR
docker push $ECR_URI:latest

Write-Host "✓ Image pushed to ECR: $ECR_URI:latest"
```

### 2.5 Create Lambda Function from Container Image

```bash
# Create IAM role for Lambda
$LAMBDA_ROLE_NAME = "cold-start-lambda-role"
$TRUST_POLICY = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"@

$TRUST_POLICY | Out-File -FilePath trust-policy.json
aws iam create-role `
    --role-name $LAMBDA_ROLE_NAME `
    --assume-role-policy-document file://trust-policy.json

# Attach S3 read policy
aws iam attach-role-policy `
    --role-name $LAMBDA_ROLE_NAME `
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

# Create Lambda function
aws lambda create-function `
    --function-name cold-start-api `
    --role arn:aws:iam::${AWS_ACCOUNT_ID}:role/$LAMBDA_ROLE_NAME `
    --code ImageUri=$ECR_URI:latest `
    --package-type Image `
    --timeout 60 `
    --memory-size 1024 `
    --region $REGION `
    --environment "Variables={MODEL_BUCKET=$BUCKET_NAME,ENVIRONMENT=production}"

Write-Host "✓ Lambda function created: cold-start-api"
```

### 2.6 Create API Gateway for Lambda

```bash
# Create REST API
$API_ID = (aws apigateway create-rest-api `
    --name "cold-start-api" `
    --description "Cold-Start Prediction API" `
    --query 'id' --output text)

# Get root resource
$ROOT_ID = (aws apigateway get-resources `
    --rest-api-id $API_ID `
    --query 'items[0].id' --output text)

# Create proxy resource
$PROXY_ID = (aws apigateway create-resource `
    --rest-api-id $API_ID `
    --parent-id $ROOT_ID `
    --path-part "{proxy+}" `
    --query 'id' --output text)

# Create method
aws apigateway put-method `
    --rest-api-id $API_ID `
    --resource-id $PROXY_ID `
    --http-method ANY `
    --authorization-type NONE

# Add Lambda integration
aws apigateway put-integration `
    --rest-api-id $API_ID `
    --resource-id $PROXY_ID `
    --http-method ANY `
    --type AWS_PROXY `
    --integration-http-method POST `
    --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:${REGION}:${AWS_ACCOUNT_ID}:function:cold-start-api/invocations"

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission `
    --function-name cold-start-api `
    --statement-id AllowAPIGateway `
    --action lambda:InvokeFunction `
    --principal apigateway.amazonaws.com `
    --source-arn "arn:aws:execute-api:${REGION}:${AWS_ACCOUNT_ID}:${API_ID}/*/*"

# Deploy API
$DEPLOYMENT = aws apigateway create-deployment `
    --rest-api-id $API_ID `
    --stage-name prod `
    --query 'id' --output text

$API_URL = "https://${API_ID}.execute-api.${REGION}.amazonaws.com/prod"
Write-Host "✓ API Gateway deployed: $API_URL"
```

---

## Phase 3: Frontend Deployment (React Dashboard)

### 3.1 Update Environment Configuration

```bash
cd dashboard/frontend

# Create .env file
@"
REACT_APP_API_BASE=https://$API_ID.execute-api.${REGION}.amazonaws.com/prod
REACT_APP_POLL_INTERVAL=30000
"@ | Out-File -FilePath .env

# Verify node_modules
npm install
```

### 3.2 Build Production Dashboard

```bash
# Create optimized production build
npm run build

# Output: dashboard/frontend/build/
# This contains static HTML/CSS/JS for deployment
```

### 3.3 Create S3 Bucket for Static Hosting

```bash
$DASHBOARD_BUCKET = "cold-start-dashboard-$(Get-Random -Minimum 1000 -Maximum 9999)"

# Create bucket
aws s3api create-bucket `
    --bucket $DASHBOARD_BUCKET `
    --region $REGION

# Enable static website hosting
$WEBSITE_CONFIG = @"
{
    "IndexDocument": {
        "Suffix": "index.html"
    },
    "ErrorDocument": {
        "Key": "index.html"
    }
}
"@

$WEBSITE_CONFIG | Out-File -FilePath website.json
aws s3api put-bucket-website `
    --bucket $DASHBOARD_BUCKET `
    --website-configuration file://website.json

Write-Host "✓ S3 bucket created: $DASHBOARD_BUCKET"
```

### 3.4 Upload Dashboard to S3

```bash
# Upload build artifacts
aws s3 sync dashboard/frontend/build/ `
    s3://$DASHBOARD_BUCKET/ `
    --delete `
    --cache-control "max-age=31536000" `
    --exclude "*.html" `
    --exclude "index.html"

# Upload HTML with no-cache
aws s3 cp dashboard/frontend/build/index.html `
    s3://$DASHBOARD_BUCKET/index.html `
    --content-type "text/html" `
    --cache-control "no-cache, no-store, must-revalidate"

Write-Host "✓ Dashboard uploaded to S3"
```

### 3.5 Deploy CloudFront Distribution

```bash
# Create CloudFront distribution
$CLOUDFRONT_CONFIG = @"
{
    "CallerReference": "$(Get-Random)",
    "DefaultCacheBehavior": {
        "TargetOriginId": "s3-origin",
        "ViewerProtocolPolicy": "redirect-to-https",
        "TrustedSigners": {
            "Enabled": false,
            "Quantity": 0
        },
        "ForwardedValues": {
            "QueryString": false,
            "Cookies": { "Forward": "none" },
            "Headers": { "Quantity": 0 }
        },
        "DefaultTTL": 86400,
        "MaxTTL": 31536000,
        "Compress": true,
        "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6"
    },
    "Origins": {
        "Quantity": 1,
        "Items": [
            {
                "Id": "s3-origin",
                "DomainName": "$DASHBOARD_BUCKET.s3.${REGION}.amazonaws.com",
                "S3OriginConfig": { "OriginAccessIdentity": "" }
            }
        ]
    },
    "Enabled": true,
    "Comment": "Cold-Start Dashboard CDN"
}
"@

$CLOUDFRONT_CONFIG | Out-File -FilePath cloudfront-config.json

$DISTRIBUTION = aws cloudfront create-distribution `
    --distribution-config file://cloudfront-config.json `
    --query 'Distribution.DomainName' --output text

Write-Host "✓ CloudFront distribution deployed: https://$DISTRIBUTION"
```

---

## Phase 4: Lambda Warmer Deployment

### 4.1 Create Lambda Warmer Function

```bash
# Lambda function code (handler.py)
# Already exists at: lambda_warmer/handler.py

# Create function
aws lambda create-function `
    --function-name cold-start-lambda-warmer `
    --runtime python3.10 `
    --role arn:aws:iam::${AWS_ACCOUNT_ID}:role/$LAMBDA_ROLE_NAME `
    --handler lambda_warmer.handler.lambda_handler `
    --zip-file fileb://lambda_warmer.zip `
    --timeout 30 `
    --memory-size 256

# Package warmer
cd lambda_warmer
Compress-Archive -Path handler.py -DestinationPath ../lambda_warmer.zip
cd ..
```

### 4.2 Create EventBridge Rule

```bash
# Create rule to trigger every 5 minutes
aws events put-rule `
    --name cold-start-warmer-schedule `
    --schedule-expression "rate(5 minutes)" `
    --state ENABLED

# Add Lambda as target
aws events put-targets `
    --rule cold-start-warmer-schedule `
    --targets "Id"="1","Arn"="arn:aws:lambda:${REGION}:${AWS_ACCOUNT_ID}:function:cold-start-lambda-warmer"

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission `
    --function-name cold-start-lambda-warmer `
    --statement-id AllowEventBridge `
    --action lambda:InvokeFunction `
    --principal events.amazonaws.com `
    --source-arn "arn:aws:events:${REGION}:${AWS_ACCOUNT_ID}:rule/cold-start-warmer-schedule"

Write-Host "✓ EventBridge rule created: cold-start-warmer-schedule"
```

---

## Phase 5: Monitoring & Logging

### 5.1 Enable CloudWatch Logging

```bash
# Create log group for API
aws logs create-log-group --log-group-name /aws/lambda/cold-start-api

# Create log group for warmer
aws logs create-log-group --log-group-name /aws/lambda/cold-start-lambda-warmer

# Set retention to 30 days
aws logs put-retention-policy `
    --log-group-name /aws/lambda/cold-start-api `
    --retention-in-days 30
```

### 5.2 Create CloudWatch Alarms

```bash
# Alarm for API errors
aws cloudwatch put-metric-alarm `
    --alarm-name cold-start-api-errors `
    --alarm-description "Alert on Lambda API errors" `
    --metric-name Errors `
    --namespace AWS/Lambda `
    --statistic Sum `
    --period 300 `
    --threshold 5 `
    --comparison-operator GreaterThanThreshold `
    --dimensions Name=FunctionName,Value=cold-start-api

# Alarm for API latency
aws cloudwatch put-metric-alarm `
    --alarm-name cold-start-api-latency `
    --alarm-description "Alert on high API latency" `
    --metric-name Duration `
    --namespace AWS/Lambda `
    --statistic Average `
    --period 300 `
    --threshold 5000 `
    --comparison-operator GreaterThanThreshold `
    --dimensions Name=FunctionName,Value=cold-start-api
```

---

## Phase 6: Cost Optimization

### 6.1 Estimate Monthly Costs

```
Lambda Invocations:
  - API: ~8,640 requests/month (1 every 5 min) = $0.02/month
  - Warmer: ~8,640 requests/month = $0.02/month

Data Transfer:
  - Dashboard CDN: ~$0.085 per GB
  - API responses: ~$0.09 per GB

Storage:
  - S3 models + dashboard: ~$0.023/GB/month
  - CloudFront caching: Included

Total Estimated: $50-100/month (varies by traffic)
```

### 6.2 Enable Lambda Reserved Concurrency

```bash
# Prevent runaway costs
aws lambda put-function-concurrency `
    --function-name cold-start-api `
    --reserved-concurrent-executions 10

aws lambda put-function-concurrency `
    --function-name cold-start-lambda-warmer `
    --reserved-concurrent-executions 5
```

---

## Phase 7: Verification & Testing

### 7.1 Test API Endpoints

```bash
# Health check
$API_URL = "https://$API_ID.execute-api.${REGION}.amazonaws.com/prod"
curl -X GET "$API_URL/health"

# Dashboard data
curl -X GET "$API_URL/dashboard-data"

# Metrics
curl -X GET "$API_URL/metrics"

# Test prediction
curl -X POST "$API_URL/predict" `
    -Headers @{'Content-Type'='application/json'} `
    -Body (ConvertTo-Json @{features=@(1.0, 2.0, 3.0)})
```

### 7.2 Verify Dashboard Access

```bash
# Navigate to CloudFront domain in browser
# https://<cloudfront-distribution-id>.cloudfront.net

# Verify:
# ✓ Dashboard loads without errors
# ✓ Charts render correctly
# ✓ Real-time data updates every 30s
# ✓ Admin controls (Retrain, Adjust Threshold) work
```

---

## Phase 8: Production Checklist

- [ ] Lambda functions deployed and tested
- [ ] API Gateway endpoints accessible
- [ ] Dashboard frontend deployed to CloudFront
- [ ] EventBridge warmer rule active
- [ ] CloudWatch logs configured
- [ ] Alarms set for errors and latency
- [ ] Reserved concurrency limits set
- [ ] Model artifacts in S3 with versioning
- [ ] Environment variables configured (ENVIRONMENT=production)
- [ ] CORS enabled for dashboard ↔ API communication
- [ ] SSL/TLS enforced (HTTPS only)
- [ ] Cost monitoring enabled in AWS Billing

---

## Phase 9: Troubleshooting

### API Gateway Returns 500 Error

```bash
# Check Lambda logs
aws logs tail /aws/lambda/cold-start-api --follow

# Verify Lambda function environment
aws lambda get-function-configuration --function-name cold-start-api

# Test locally first
uvicorn src.api.app:app --reload
```

### Dashboard Won't Connect to API

```bash
# Check CloudFront cache
aws cloudfront create-invalidation `
    --distribution-id $DISTRIBUTION_ID `
    --paths "/*"

# Verify CORS settings in FastAPI app.py
# Should have: app.add_middleware(CORSMiddleware, ...)

# Check browser console for CORS errors
```

### EventBridge Rule Not Triggering

```bash
# Verify rule is enabled
aws events describe-rule `
    --name cold-start-warmer-schedule

# Check target status
aws events list-targets-by-rule `
    --rule cold-start-warmer-schedule

# View Lambda invocation metrics
aws cloudwatch get-metric-statistics `
    --namespace AWS/Lambda `
    --metric-name Invocations `
    --dimensions Name=FunctionName,Value=cold-start-lambda-warmer `
    --start-time 2024-01-01T00:00:00Z `
    --end-time 2024-01-02T00:00:00Z `
    --period 3600 `
    --statistics Sum
```

---

## Phase 10: Cleanup & Rollback

To remove all deployed resources:

```bash
# Delete API Gateway
aws apigateway delete-rest-api --rest-api-id $API_ID

# Delete Lambda functions
aws lambda delete-function --function-name cold-start-api
aws lambda delete-function --function-name cold-start-lambda-warmer

# Delete EventBridge rule
aws events remove-targets --rule cold-start-warmer-schedule --ids "1"
aws events delete-rule --name cold-start-warmer-schedule

# Delete CloudFront distribution
aws cloudfront delete-distribution --id $DISTRIBUTION_ID --etag <etag>

# Empty and delete S3 buckets
aws s3 rm s3://$BUCKET_NAME --recursive
aws s3api delete-bucket --bucket $BUCKET_NAME
aws s3 rm s3://$DASHBOARD_BUCKET --recursive
aws s3api delete-bucket --bucket $DASHBOARD_BUCKET

# Delete IAM role
aws iam detach-role-policy `
    --role-name $LAMBDA_ROLE_NAME `
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess
aws iam delete-role --role-name $LAMBDA_ROLE_NAME

# Delete CloudWatch logs
aws logs delete-log-group --log-group-name /aws/lambda/cold-start-api
aws logs delete-log-group --log-group-name /aws/lambda/cold-start-lambda-warmer

# Delete ECR repository
aws ecr delete-repository `
    --repository-name cold-start-api `
    --force
```

---

## Quick Reference: Key Outputs

Save these for future reference:

```
API Endpoint: https://<api-id>.execute-api.us-east-1.amazonaws.com/prod
Dashboard URL: https://<cloudfront-id>.cloudfront.net
S3 Model Bucket: cold-start-models-XXXX
S3 Dashboard Bucket: cold-start-dashboard-XXXX
Lambda Functions:
  - cold-start-api
  - cold-start-lambda-warmer
EventBridge Rule: cold-start-warmer-schedule
CloudWatch Logs:
  - /aws/lambda/cold-start-api
  - /aws/lambda/cold-start-lambda-warmer
```

---

**Last Updated:** 2026-04-13
