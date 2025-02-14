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