#!/bin/bash

# Default region
DEFAULT_REGION="us-west-2"
REGION=${1:-$DEFAULT_REGION}

# Show usage if help flag is provided
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $0 [aws-region]"
    echo "Example: $0 us-west-2"
    echo ""
    echo "Default region: $DEFAULT_REGION"
    exit 0
fi

# Get all active sessions
SESSIONS=$(aws ssm describe-sessions \
    --state "Active" \
    --region "$REGION" \
    --query 'Sessions[*].SessionId' \
    --output text)

if [ -z "$SESSIONS" ]; then
    echo "No active sessions found."
else
    echo "Terminating active SSM sessions..."
    for SESSION_ID in $SESSIONS; do
        aws ssm terminate-session \
            --session-id "$SESSION_ID" \
            --region "$REGION"
    done
fi

# Kill local port forwarding processes
pkill -f 'aws ssm start-session'

# Verify termination
REMAINING_SESSIONS=$(aws ssm describe-sessions \
    --state "Active" \
    --region "$REGION" \
    --query 'Sessions[*].SessionId' \
    --output text)

if [ -z "$REMAINING_SESSIONS" ]; then
    echo "All sessions terminated successfully."
else
    echo "Warning: Some sessions may still be active."
fi

# Check remaining processes
ps aux | grep -i "ssm" | grep -v grep || echo "No SSM processes found."
