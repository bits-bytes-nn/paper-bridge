#!/bin/bash

# Update system packages
yum update -y

# Enable and start SSM agent
systemctl enable amazon-ssm-agent
systemctl start amazon-ssm-agent

# Get AWS region from instance metadata
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
REGION=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

# Configure SSM agent
mkdir -p /etc/amazon/ssm
cat > /etc/amazon/ssm/amazon-ssm-agent.json << EOF
{
    "Profile": {
        "CredentialProfile": "",
        "ProfilePath": ""
    },
    "Mds": {
        "CommandWorkersLimit": 5,
        "StopTimeoutInSeconds": 20,
        "Endpoint": "",
        "CommandRetryLimit": 15
    },
    "Ssm": {
        "Endpoint": "",
        "HealthFrequencyMinutes": 5,
        "CustomInventoryDefaultLocation": "",
        "AssociationLogsRetentionDurationHours": 24,
        "RunCommandLogsRetentionDurationHours": 336,
        "SessionLogsRetentionDurationHours": 336
    },
    "Agent": {
        "Region": "${REGION}",
        "OrchestrationDirectoryPath": "",
        "SelfUpdate": true
    },
    "Plugins": {
        "aws:SessionManagerPortForwarding": {
            "enabled": true,
            "properties": {
                "portNumber": "0",
                "localPortNumber": "0"
            }
        }
    }
}
EOF

# Configure system for optimal port forwarding
cat >> /etc/sysctl.conf << EOF
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_fin_timeout = 30
net.core.somaxconn = 65535
EOF
sysctl -p

# Restart SSM agent and cleanup
systemctl restart amazon-ssm-agent
yum clean all
rm -rf /var/cache/yum
