output "vpc" {
  description = "VPC details including ID, CIDR block, and ARN"
  value = {
    id         = aws_vpc.this.id
    cidr_block = aws_vpc.this.cidr_block
    arn        = aws_vpc.this.arn
  }
}

output "subnets" {
  description = "Detailed information about public and private subnets"
  value = {
    public = [
      for subnet in aws_subnet.public : {
        id         = subnet.id
        cidr_block = subnet.cidr_block
        az         = subnet.availability_zone
      }
    ]
    private = [
      for subnet in aws_subnet.private : {
        id         = subnet.id
        cidr_block = subnet.cidr_block
        az         = subnet.availability_zone
      }
    ]
  }
}

output "public_subnet_ids" {
  description = "List of public subnet IDs for external-facing resources"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs for internal resources"
  value       = aws_subnet.private[*].id
}

output "vpn_security_group_ids" {
  description = "List of VPN security group IDs for secure access"
  value       = var.enable_vpn ? [aws_security_group.vpn[0].id] : []
}

output "vpn_endpoint" {
  description = "VPN endpoint details for client connections"
  value       = var.enable_vpn ? aws_ec2_client_vpn_endpoint.this[0].dns_name : null
}

output "nat_gateway_ips" {
  description = "NAT Gateway Elastic IP addresses for outbound internet access"
  value       = aws_eip.nat[*].public_ip
}
