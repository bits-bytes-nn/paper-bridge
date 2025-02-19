output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}
output "vpn_security_group_id" {
  description = "ID of the VPN security group"
  value       = var.enable_vpn ? aws_security_group.vpn[0].id : null
}
