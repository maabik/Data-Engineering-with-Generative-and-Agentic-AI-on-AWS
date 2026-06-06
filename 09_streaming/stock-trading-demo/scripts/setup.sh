#!/bin/bash

# Exit on error
set -e

echo "Setting up Stock Trading Demo..."

# Install CDK dependencies
echo "Installing CDK dependencies..."
cd ../cdk
pip install -r requirements.txt

# Install Lambda dependencies
echo "Installing Lambda dependencies..."
cd ../lambda
pip install -r requirements.txt -t .

# Install script dependencies
echo "Installing script dependencies..."
cd ../scripts
pip install -r requirements.txt
pip install tabulate  # For query_trades.py

echo "Deploying infrastructure with CDK..."
cd ../cdk
cdk bootstrap
cdk deploy

# Get outputs from CDK
echo "Getting deployment outputs..."
MSK_BOOTSTRAP_SERVERS=$(aws cloudformation describe-stacks --stack-name StockTradingStack --query "Stacks[0].Outputs[?OutputKey=='MSKBootstrapServers'].OutputValue" --output text)
QUERY_RESULTS_BUCKET=$(aws cloudformation describe-stacks --stack-name StockTradingStack --query "Stacks[0].Outputs[?OutputKey=='S3TablesBucket'].OutputValue" --output text)

# Update scripts with actual values
echo "Updating scripts with deployment values..."
cd ../scripts

# Update trade_generator.py
sed -i '' "s|YOUR_MSK_BOOTSTRAP_SERVERS|$MSK_BOOTSTRAP_SERVERS|g" trade_generator.py

# Update query_trades.py and update_trade.py
sed -i '' "s|REPLACE_WITH_QUERY_RESULTS_BUCKET|$QUERY_RESULTS_BUCKET|g" query_trades.py
sed -i '' "s|REPLACE_WITH_QUERY_RESULTS_BUCKET|$QUERY_RESULTS_BUCKET|g" update_trade.py

echo "Setup complete!"
echo "To start generating trades, run: python trade_generator.py"
echo "To query trades, run: python query_trades.py"
echo "To update trades, run: python update_trade.py <trade_id> --price <new_price> --quantity <new_quantity>"
