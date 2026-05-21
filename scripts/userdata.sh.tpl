#!/bin/bash
###############################################################
# userdata.sh.tpl
# Runs once on first boot as root.
# Installs: Docker, PostgreSQL, CloudWatch Agent
###############################################################
set -exo pipefail
exec > /var/log/userdata.log 2>&1

PROJECT="${project}"
DB_NAME="${db_name}"
DB_USER="${db_user}"
DB_PASS="${db_password}"
APP_PORT="${app_port}"
AWS_REGION="${aws_region}"

##############################################
# 1. System update
##############################################
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y curl wget unzip gnupg2 software-properties-common \
  ca-certificates lsb-release jq

##############################################
# 2. Install Docker
##############################################
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

##############################################
# 3. Install PostgreSQL 15
##############################################
apt-get install -y postgresql postgresql-contrib

systemctl enable postgresql
systemctl start postgresql

# Create DB + user
sudo -u postgres psql <<SQL
CREATE DATABASE $DB_NAME;
CREATE USER $DB_USER WITH ENCRYPTED PASSWORD '$DB_PASS';
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\c $DB_NAME
GRANT ALL ON SCHEMA public TO $DB_USER;
SQL

# Allow password auth from localhost
PG_HBA=$(find /etc/postgresql -name pg_hba.conf | head -1)
sed -i "s/^local\s\+all\s\+all\s\+peer/local all all md5/" "$PG_HBA"
systemctl reload postgresql

##############################################
# 4. Install AWS CloudWatch Agent
##############################################
wget -q https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb \
  -O /tmp/amazon-cloudwatch-agent.deb
dpkg -i /tmp/amazon-cloudwatch-agent.deb

# Write CloudWatch agent config
mkdir -p /opt/aws/amazon-cloudwatch-agent/etc
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "cwagent"
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/syslog",
            "log_group_name": "/ec2/$PROJECT/system",
            "log_stream_name": "{instance_id}/syslog",
            "timezone": "UTC"
          },
          {
            "file_path": "/var/log/userdata.log",
            "log_group_name": "/ec2/$PROJECT/system",
            "log_stream_name": "{instance_id}/userdata",
            "timezone": "UTC"
          },
          {
            "file_path": "/var/log/app/*.log",
            "log_group_name": "/ec2/$PROJECT/app",
            "log_stream_name": "{instance_id}/app",
            "timezone": "UTC"
          }
        ]
      }
    }
  },
  "metrics": {
    "append_dimensions": {
      "InstanceId": "{{.InstanceId}}"
    },
    "metrics_collected": {
      "mem": { "measurement": ["mem_used_percent"] },
      "disk": { "measurement": ["disk_used_percent"],
                "resources": ["/"] }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

systemctl enable amazon-cloudwatch-agent
systemctl start amazon-cloudwatch-agent

##############################################
# 5. Docker log driver → CloudWatch
##############################################
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "$AWS_REGION",
    "awslogs-group": "/ec2/$PROJECT/docker",
    "awslogs-stream": "container-logs",
    "awslogs-create-group": "true"
  }
}
EOF
systemctl restart docker

##############################################
# 6. Create app log directory
##############################################
mkdir -p /var/log/app
chown ubuntu:ubuntu /var/log/app

##############################################
# 7. Pull and run a sample Nginx container
#    Replace this block with your actual app image
##############################################
docker run -d \
  --name sample-app \
  --restart unless-stopped \
  -p $APP_PORT:80 \
  nginx:alpine

echo "Bootstrap complete at $(date)" >> /var/log/userdata.log
