# Step 2: Create IAM execution role (if it doesn't exist)
echo "🔐 Step 2: Ensuring IAM execution role exists..."
ROLE_NAME="RedshiftMCPExecutionRole"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Create role and policy
aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document file://trust-policy.json
aws iam put-role-policy --role-name $ROLE_NAME --policy-name RedshiftMCPPermissions --policy-document file://permissions-policy.json

