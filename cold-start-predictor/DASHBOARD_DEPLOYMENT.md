# Dashboard Build & Deployment Commands

## Local Development

### Install Dependencies

```bash
cd dashboard/frontend
npm install
```

### Start Development Server

```bash
npm start
# Opens http://localhost:3000
# Proxy to http://localhost:8000 (FastAPI backend)
```

### Build for Production

```bash
npm run build
# Creates optimized build in ./build/ directory
```

---

## AWS Deployment (using PowerShell on Windows)

### Step 1: Set Environment Variables

```powershell
$REGION = "us-east-1"
$AWS_ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$BUCKET_NAME = "cold-start-dashboard-$(Get-Random -Minimum 1000 -Maximum 9999)"
$DISTRIBUTION_ID = "Your-Distribution-ID-Here"  # Set after CloudFront deployment
```

### Step 2: Build Dashboard

```powershell
cd dashboard/frontend
npm install
npm run build
cd ../..
```

### Step 3: Deploy Dashboard

```powershell
# Sync to S3
aws s3 sync dashboard/frontend/build/ `
    s3://$BUCKET_NAME/ `
    --delete `
    --cache-control "max-age=31536000" `
    --exclude "*.html"

# Upload index.html with no-cache
aws s3 cp dashboard/frontend/build/index.html `
    s3://$BUCKET_NAME/index.html `
    --content-type "text/html" `
    --cache-control "no-cache, no-store, must-revalidate"

Write-Host "✓ Dashboard deployed to s3://$BUCKET_NAME"
```

### Step 4: Invalidate CloudFront Cache

```powershell
aws cloudfront create-invalidation `
    --distribution-id $DISTRIBUTION_ID `
    --paths "/*"

Write-Host "✓ CloudFront cache invalidated"
```

---

## Docker Build & Push (for API)

```powershell
$REPO = "cold-start-api"
$TAG = "latest"
$ECR_URI = "${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

# Build image
docker build -t $ECR_URI .

# Login to ECR
$token = (aws ecr get-authorization-token --region $REGION --query 'authorizationData[0].authorizationToken' --output text)
echo $token | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Push to ECR
docker push $ECR_URI

Write-Host "✓ Docker image pushed: $ECR_URI"
```

---

## Verify Deployment

```powershell
# Test API
$API_URL = "https://<api-id>.execute-api.${REGION}.amazonaws.com/prod"
curl -X GET "$API_URL/health"

# Test Dashboard
# Open: https://<cloudfront-id>.cloudfront.net in browser
```
