###############################################################
# outputs.tf
###############################################################

output "ec2_public_ip" {
  description = "Public IP of the EC2 instance"
  value       = aws_instance.main.public_ip
}

output "ec2_public_dns" {
  description = "Public DNS of the EC2 instance"
  value       = aws_instance.main.public_dns
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ~/.ssh/ec2_key ubuntu@${aws_instance.main.public_ip}"
}

output "cloudwatch_log_groups" {
  description = "CloudWatch log group names"
  value = {
    app      = aws_cloudwatch_log_group.app.name
    system   = aws_cloudwatch_log_group.system.name
    docker   = aws_cloudwatch_log_group.docker.name
    database = aws_cloudwatch_log_group.database.name
  }
}

output "instance_id" {
  description = "EC2 Instance ID"
  value       = aws_instance.main.id
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.ec2.id
}
