#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${AWS_PROFILE:-hadar-pc}"
REGION="${AWS_REGION:-us-east-1}"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

echo "==> Packaging Lambda"
bash "$ROOT/scripts/package_lambda.sh"

echo "==> Terraform init/apply"
cd "$ROOT/terraform"
if [[ ! -f terraform.tfvars ]]; then
  cp terraform.tfvars.example terraform.tfvars
  echo "Created terraform/terraform.tfvars from example"
fi

# In CI, prefer empty profile (env credentials).
if [[ "${CI:-}" == "true" || "${GITHUB_ACTIONS:-}" == "true" ]]; then
  TF_VAR_aws_profile=""
  export TF_VAR_aws_profile
fi

terraform init -input=false
terraform apply -input=false -auto-approve

API_URL="$(terraform output -raw api_url)"
FRONTEND_BUCKET="$(terraform output -raw frontend_bucket)"
CF_URL="$(terraform output -raw cloudfront_url)"
FULL_SCAN="$(terraform output -raw full_scan_lambda)"

echo "==> Writing frontend config.js"
cat > "$ROOT/frontend/config.js" <<EOF
window.FRINGE_CONFIG = {
  apiUrl: "${API_URL}",
};
EOF

echo "==> Syncing frontend to s3://${FRONTEND_BUCKET}"
aws s3 sync "$ROOT/frontend/" "s3://${FRONTEND_BUCKET}/" \
  --delete \
  --cache-control "max-age=60" \
  --exclude ".DS_Store"

DIST_ID="$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='fringe-monitor'].Id | [0]" \
  --output text)"
if [[ -n "${DIST_ID}" && "${DIST_ID}" != "None" ]]; then
  echo "==> Invalidating CloudFront ${DIST_ID}"
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null
fi

echo
echo "Deploy complete."
echo "  Frontend: ${CF_URL}"
echo "  API:      ${API_URL}"
echo
echo "IMPORTANT: Confirm the SES verification email sent to hadarwaldman@gmail.com"
echo "Optional first scan:"
echo "  AWS_PROFILE=${PROFILE} aws lambda invoke --function-name ${FULL_SCAN} /tmp/fringe-full-scan.json && cat /tmp/fringe-full-scan.json"
