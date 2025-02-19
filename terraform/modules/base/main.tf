data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name = "${var.project_name}-vpc"
  })
}

resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.project_name}-default-sg"
  })
}

resource "aws_subnet" "public" {
  count                   = var.max_azs
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.project_name}-public-${count.index + 1}"
  })
}

resource "aws_subnet" "private" {
  count             = var.max_azs
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index + var.max_azs)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(var.tags, {
    Name = "${var.project_name}-private-${count.index + 1}"
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.project_name}-igw"
  })
}

resource "aws_eip" "nat" {
  count  = var.nat_gateways
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${var.project_name}-nat-eip-${count.index + 1}"
  })
}

resource "aws_nat_gateway" "this" {
  count         = var.nat_gateways
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.tags, {
    Name = "${var.project_name}-nat-${count.index + 1}"
  })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-public"
  })
}

resource "aws_route_table" "private" {
  count  = var.max_azs
  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index % var.nat_gateways].id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-private-${count.index + 1}"
  })
}

resource "aws_route_table_association" "public" {
  count          = var.max_azs
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = var.max_azs
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_acm_certificate" "server" {
  count             = var.enable_vpn ? 1 : 0
  private_key       = file("${path.module}/certificates/server.vpn.internal.key")
  certificate_body  = file("${path.module}/certificates/server.vpn.internal.crt")
  certificate_chain = file("${path.module}/certificates/ca.crt")

  tags = merge(var.tags, {
    Name = "${var.project_name}-vpn-server-cert"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "client" {
  count             = var.enable_vpn ? 1 : 0
  private_key       = file("${path.module}/certificates/client.vpn.internal.key")
  certificate_body  = file("${path.module}/certificates/client.vpn.internal.crt")
  certificate_chain = file("${path.module}/certificates/ca.crt")

  tags = merge(var.tags, {
    Name = "${var.project_name}-vpn-client-cert"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "vpn" {
  count  = var.enable_vpn ? 1 : 0
  name   = "${var.project_name}-vpn-sg"
  vpc_id = aws_vpc.this.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "udp"
    cidr_blocks = [var.vpn_client_cidr_block]
    description = "VPN Access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-vpn-sg"
  })
}

resource "aws_ec2_client_vpn_endpoint" "this" {
  count                  = var.enable_vpn ? 1 : 0
  description           = "${var.project_name} Client VPN"
  server_certificate_arn = aws_acm_certificate.server[0].arn
  client_cidr_block     = var.vpn_client_cidr_block
  security_group_ids    = [aws_security_group.vpn[0].id]

  authentication_options {
    type                       = "certificate-authentication"
    root_certificate_chain_arn = aws_acm_certificate.client[0].arn
  }

  connection_log_options {
    enabled = false
  }

  split_tunnel = true
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.project_name}-client-vpn"
  })
}

resource "aws_ec2_client_vpn_network_association" "this" {
  count = var.enable_vpn ? var.max_azs : 0

  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  subnet_id              = aws_subnet.private[count.index].id

  depends_on = [aws_ec2_client_vpn_endpoint.this]
}

resource "aws_ec2_client_vpn_authorization_rule" "this" {
  count = var.enable_vpn ? 1 : 0

  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  target_network_cidr    = aws_vpc.this.cidr_block
  authorize_all_groups   = true

  depends_on = [aws_ec2_client_vpn_endpoint.this, aws_ec2_client_vpn_network_association.this]
}

resource "aws_ec2_client_vpn_route" "this" {
  count = var.enable_vpn ? var.max_azs : 0

  client_vpn_endpoint_id = aws_ec2_client_vpn_endpoint.this[0].id
  destination_cidr_block = "0.0.0.0/0"
  target_vpc_subnet_id   = aws_subnet.private[count.index].id

  depends_on = [aws_ec2_client_vpn_network_association.this]
}
