#!/bin/bash

# Default values
DEFAULT_PROJECT_NAME="paper-bridge-dev"
DEFAULT_REGION="us-west-2"

# Parse command line arguments
PROJECT_NAME=${1:-$DEFAULT_PROJECT_NAME}
AWS_REGION=${2:-$DEFAULT_REGION}

# Show usage if help flag is provided
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $0 [project-name] [aws-region]"
    echo "Example: $0 paper-bridge-dev us-west-2"
    echo ""
    echo "Default values:"
    echo "  Project name: $DEFAULT_PROJECT_NAME"
    echo "  Region: $DEFAULT_REGION"
    exit 0
fi

# Get parameter values from SSM Parameter Store
BASTION_ID=$(aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "/${PROJECT_NAME}/bastion-host/instance-id" \
    --query "Parameter.Value" \
    --output text)

NEPTUNE_ENDPOINT=$(aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "/${PROJECT_NAME}/neptune/endpoint" \
    --query "Parameter.Value" \
    --output text)

OPENSEARCH_ENDPOINT=$(aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "/${PROJECT_NAME}/opensearch/endpoint" \
    --query "Parameter.Value" \
    --output text)

# Extract hostnames from endpoints
NEPTUNE_HOST=${NEPTUNE_ENDPOINT#neptune-db://}
NEPTUNE_HOST=${NEPTUNE_HOST%:*}
OPENSEARCH_HOST=${OPENSEARCH_ENDPOINT#https://}
OPENSEARCH_HOST=${OPENSEARCH_HOST%:*}

# Function to start port forwarding session
start_port_forwarding() {
    local target=$1
    local remote_port=$2
    local host=$3
    local service=$4
    local local_port=${5:-$remote_port}

    echo "Starting port forwarding for $service..."
    aws ssm start-session \
        --region "$AWS_REGION" \
        --target "$target" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"$host\"],\"portNumber\":[\"$remote_port\"],\"localPortNumber\":[\"$local_port\"]}" &

    sleep 2
}

# Start port forwarding sessions
start_port_forwarding "$BASTION_ID" "8182" "$NEPTUNE_HOST" "Neptune"
start_port_forwarding "$BASTION_ID" "443" "$OPENSEARCH_HOST" "OpenSearch" "8443"

echo -e "\nPort forwarding sessions started:"
echo "Neptune: localhost:8182 -> $NEPTUNE_HOST:8182"
echo "OpenSearch: localhost:8443 -> $OPENSEARCH_HOST:443"
echo -e "\nTo stop port forwarding sessions: pkill -f 'aws ssm start-session'"
