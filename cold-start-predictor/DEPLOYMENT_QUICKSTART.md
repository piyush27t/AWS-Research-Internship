# 🚀 AWS Deployment: Quick Start

**Complete deployment in 10 steps**

---

## STEP 1: Prepare Local Environment

```powershell
# Install required tools
# - AWS CLI: https://awscli.amazonaws.com/AWSCLIV2.msi
# - Docker Desktop: https://www.docker.com/products/docker-desktop
# - Node.js: https://nodejs.org/

# Verify installations
aws --version          # AWS CLI 2.x
docker --version       # Docker 20.x
node --version         # Node 16+
npm --version          # npm 8+

# Configure AWS credentials
aws configure
# Enter: Access Key ID, Secret Access Key, Region (us-east-1), Format (json)
```

---

## STEP 2: Create S3 Bucket for Models

```powershell
$BUCKET = "cold-start-models-$(Get-Random -Minimum 100-9999)"
$REGION = "us-east-1"

aws s3api create-bucket --bucket $BUCKET --region $REGION

# Upload trained models
aws s3 cp ./models/ s3://$BUCKET/models/ --recursive

Write-Host "✓ Model bucket: $BUCKET"
```

---

## STEP 3: Create ECR Repository

```powershell
$AWS_ID = aws sts get-caller-identity --query Account --output text
$ECR = "${AWS_ID}.dkr.ecr.${REGION}.amazonaws.com/cold-start-api"

aws ecr create-repository --repository-name cold-start-api --region $REGION

# Login
$token = aws ecr get-authorization-token --region $REGION `
    --query 'authorizationData[0].authorizationToken' --output text
echo $token | docker login --username AWS --password-stdin `
    "${AWS_ID}.dkr.ecr.${REGION}.amazonaws.com"

Write-Host "✓ ECR repository created"
```

---

## STEP 4: Build & Push Docker Image

```powershell
# Build image
docker build -t "${AWS_ID}.dkr.ecr.${REGION}.amazonaws.com/cold-start-api:latest" .

# Push to ECR
docker push "${AWS_ID}.dkr.ecr.${REGION}.amazonaws.com/cold-start-api:latest"

Write-Host "✓ API image pushed to ECR"
```

---

## STEP 5: Deploy Lambda Function

```powershell
# Create IAM role
$TRUST = '[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]'
aws iam create-role --role-name cold-start-lambda --assume-role-policy-document $TRUST

aws iam attach-role-policy --role-name cold-start-lambda `
    --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

# Deploy Lambda from ECR
aws lambda create-function `
    --function-name cold-start-api `
    --role arn:aws:iam::${AWS_ID}:role/cold-start-lambda `
    --code ImageUri="${AWS_ID}.dkr.ecr.${REGION}.amazonaws.com/cold-start-api:latest" `
    --package-type Image `
    --timeout 60 `
    --memory-size 1024 `
    --environment "Variables={MODEL_BUCKET=$BUCKET}"

Write-Host "✓ Lambda function deployed"
```

---

## STEP 6: Create API Gateway

```powershell
# Create REST API
$API = aws apigateway create-rest-api `
    --name cold-start-api `
    --query 'id' --output text

# Get root resource
$ROOT = aws apigateway get-resources --rest-api-id $API `
    --query 'items[0].id' --output text

# Create proxy
$PROXY = aws apigateway create-resource --rest-api-id $API `
    --parent-id $ROOT --path-part "{proxy+}" `
    --query 'id' --output text

# Create method
aws apigateway put-method --rest-api-id $API --resource-id $PROXY `
    --http-method ANY --authorization-type NONE

# Add Lambda integration
aws apigateway put-integration `
    --rest-api-id $API --resource-id $PROXY --http-method ANY `
    --type AWS_PROXY `
    --uri "arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/arn:aws:lambda:${REGION}:${AWS_ID}:function:cold-start-api/invocations"

# Grant permission
aws lambda add-permission `
    --function-name cold-start-api `
    --statement-id AllowAPI `
    --action lambda:InvokeFunction `
    --principal apigateway.amazonaws.com

# Deploy
aws apigateway create-deployment --rest-api-id $API --stage-name prod

$API_URL = "https://${API}.execute-api.${REGION}.amazonaws.com/prod"
Write-Host "✓ API Gateway deployed: $API_URL"
```

---

## STEP 7: Build Dashboard

```powershell
cd dashboard/frontend

npm install
npm run build

cd ../..
Write-Host "✓ Dashboard built: ./dashboard/frontend/build/"
```

---

## STEP 8: Deploy Dashboard to S3 + CloudFront

```powershell
$DASHBOARD_BUCKET = "cold-start-dashboard-$(Get-Random -Minimum 1000-9999)"

# Create S3 bucket
aws s3api create-bucket --bucket $DASHBOARD_BUCKET --region $REGION

# Enable static hosting
$CONFIG = '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}'
aws s3api put-bucket-website --bucket $DASHBOARD_BUCKET --website-configuration $CONFIG

# Upload dashboard
aws s3 sync dashboard/frontend/build/ s3://$DASHBOARD_BUCKET/ --delete

# Create CloudFront distribution
$CF_CONFIG = @"
{
    "CallerReference": "$(Get-Random)",
    "DefaultCacheBehavior": {
        "TargetOriginId": "s3",
        "ViewerProtocolPolicy": "redirect-to-https",
        "TrustedSigners": {"Enabled": false, "Quantity": 0},
        "ForwardedValues": {"QueryString": false},
        "Compress": true,
        "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6"
    },
    "Origins": {
        "Quantity": 1,
        "Items": [{"Id": "s3", "DomainName": "$DASHBOARD_BUCKET.s3.${REGION}.amazonaws.com"}]
    },
    "Enabled": true
}
"@

$CF = aws cloudfront create-distribution --distribution-config $CF_CONFIG `
    --query 'Distribution.DomainName' --output text

Write-Host "✓ Dashboard deployed: https://$CF"
```

---

## STEP 9: Create EventBridge Warmer

```powershell
# Create EventBridge rule (triggers every 5 minutes)
aws events put-rule --name cold-start-warmer `
    --schedule-expression "rate(5 minutes)" --state ENABLED

# Add Lambda warmer as target
aws events put-targets --rule cold-start-warmer `
    --targets "Id=1,Arn=arn:aws:lambda:${REGION}:${AWS_ID}:function:cold-start-lambda-warmer"

# Grant permission
aws lambda add-permission `
    --function-name cold-start-lambda-warmer `
    --statement-id AllowEventBridge `
    --action lambda:InvokeFunction `
    --principal events.amazonaws.com

Write-Host "✓ EventBridge warmer configured"
```

---

## STEP 10: Verify Deployment

```powershell
# Test API health
curl -X GET "$API_URL/health"

# Test dashboard endpoint
curl -X GET "$API_URL/dashboard-data"

# Open dashboard in browser
# https://<cloudfront-domain>

Write-Host "✓ All services deployed successfully!"
```

---

## Summary

| Component     | Deployed             | URL                    |
| ------------- | -------------------- | ---------------------- |
| **API**       | Lambda + API Gateway | `$API_URL`             |
| **Dashboard** | S3 + CloudFront      | `https://<cloudfront>` |
| **Models**    | S3                   | `s3://$BUCKET`         |
| **Warmer**    | Lambda + EventBridge | Every 5 min            |

---

## Next Steps

1. **Monitor**: Watch CloudWatch logs for errors
2. **Test**: Send requests to `/predict` endpoint
3. **Adjust**: Fine-tune policy thresholds in config.yaml
4. **Optimize**: Review costs and set CloudWatch alarms
5. **Scale**: Update Lambda memory/concurrency as needed

---

**For detailed steps, see [AWS_DEPLOYMENT_GUIDE.md](AWS_DEPLOYMENT_GUIDE.md)**
