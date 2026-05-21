###############################################################
# variables.tf
###############################################################

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "myapp"
}

variable "environment" {
  description = "Environment tag (dev / staging / prod)"
  type        = string
  default     = "dev"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium" # 2 vCPU, 4 GB — enough for Docker + PostgreSQL
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 30
}

variable "public_key_material" {
  description = "SSH public key content (e.g. contents of ~/.ssh/id_rsa.pub)"
  type        = string
  sensitive   = true
}

variable "ssh_allowed_cidr" {
  description = "CIDR block allowed to SSH. Use your IP: curl ifconfig.me"
  type        = string
  default     = "0.0.0.0/0" # Restrict this to your IP in production!
}

variable "app_port" {
  description = "Port your Docker app listens on"
  type        = number
  default     = 8080
}

# ---- Database -------------------------------------------------

variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "appdb"
}

variable "db_user" {
  description = "PostgreSQL username"
  type        = string
  default     = "appuser"
}

variable "db_password" {
  description = "PostgreSQL password — use a strong value!"
  type        = string
  sensitive   = true
}

# ---- CloudWatch -----------------------------------------------

variable "log_retention_days" {
  description = "Days to retain CloudWatch logs"
  type        = number
  default     = 30
}
