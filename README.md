# EC2 + Docker + PostgreSQL + CloudWatch — Terraform Setup

Deploy a Linux EC2 instance with Docker, PostgreSQL, a sample Python app, and CloudWatch log shipping — all automated via Terraform.

---

## Architecture

```
Your Machine (Terraform)
        │
        ▼
  AWS Account
        │
        └── EC2 Instance (Ubuntu 22.04)
                │
                ├── Docker Container  →  Python Flask App (port 8080)
                │       │
                │       └── Connects to PostgreSQL on host
                │
                ├── PostgreSQL 15 (installed on host OS)
                │
                └── CloudWatch Agent
                        │
                        ├── /ec2/<project>/app      ← Flask app logs
                        ├── /ec2/<project>/docker   ← Docker container logs
                        └── /ec2/<project>/system   ← syslog, userdata logs
```

---

## Project Structure

```
tf-ec2-project/
├── README.md                  ← You are here
├── main.tf                    ← VPC, EC2, IAM, CloudWatch log groups
├── variables.tf               ← All input variables
├── outputs.tf                 ← IP, SSH command, log group names
├── terraform.tfvars           ← YOUR values (edit before running)
├── docker-compose.yml         ← App container config (upload to EC2)
├── .gitignore
└── scripts/
    └── userdata.sh.tpl        ← EC2 bootstrap script (runs on first boot)

app/                           ← Sample Python Flask app
├── Dockerfile
├── requirements.txt
├── app.py                     ← Flask app with PostgreSQL connection
└── docker-compose.yml         ← Override for local dev
```

---

## Prerequisites

Install these on your local machine before starting.

### 1. Terraform CLI

```bash
# Ubuntu / Debian
sudo apt-get install -y gnupg software-properties-common curl
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor \
  -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform

# Verify
terraform -version
```

### 2. AWS CLI v2

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip
sudo ./aws/install

# Verify
aws --version
```

### 3. SSH Key Pair

```bash
# Skip this if you already have ~/.ssh/id_rsa
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa

# Copy the public key — you'll paste it in terraform.tfvars
cat ~/.ssh/id_rsa.pub
```

---

## Step-by-Step Execution

### Step 1 — Configure AWS Credentials

You need an IAM user with at minimum these permissions:
- `AmazonEC2FullAccess`
- `IAMFullAccess`
- `CloudWatchFullAccess`

```bash
aws configure
# AWS Access Key ID:     AKIA...
# AWS Secret Access Key: abc123...
# Default region name:   us-east-1
# Default output format: json

# Verify it works
aws sts get-caller-identity
```

---

### Step 2 — Edit terraform.tfvars

Open `terraform.tfvars` and fill in these values:

```hcl
# Your region
aws_region   = "us-east-1"
project_name = "myapp"

# Paste output of: cat ~/.ssh/id_rsa.pub
public_key_material = "ssh-rsa AAAAB3Nza..."

# Your public IP — run: curl ifconfig.me
# Add /32 at the end
ssh_allowed_cidr = "203.0.113.10/32"

# PostgreSQL credentials
db_name     = "appdb"
db_user     = "appuser"
db_password = "Change_Me_Strong_P@ssw0rd!"
```

---

### Step 3 — Deploy with Terraform

```bash
cd tf-ec2-project

# Download AWS provider plugin
terraform init

# Preview all resources that will be created (no changes yet)
terraform plan

# Create everything — type 'yes' when prompted
terraform apply
```

Terraform will create:
- VPC, subnet, internet gateway, route table
- Security group (SSH + port 8080)
- IAM role with CloudWatch permissions
- SSH key pair
- EC2 instance (t3.medium, Ubuntu 22.04, 30 GB gp3)
- 3 CloudWatch log groups

At the end, Terraform prints outputs like:

```
ec2_public_ip = "54.123.45.67"
ssh_command   = "ssh -i ~/.ssh/id_rsa ubuntu@54.123.45.67"
cloudwatch_log_groups = {
  app    = "/ec2/myapp/app"
  docker = "/ec2/myapp/docker"
  system = "/ec2/myapp/system"
}
```

---

### Step 4 — Wait for Bootstrap to Complete

The EC2 userdata script runs automatically on first boot and installs:
- Docker + Docker Compose plugin
- PostgreSQL 15
- CloudWatch Agent (configured to ship logs)

This takes about **3–5 minutes** after `terraform apply` finishes.

```bash
# SSH into the server
ssh -i ~/.ssh/id_rsa ubuntu@YOUR_EC2_IP

# Watch the bootstrap log
tail -f /var/log/userdata.log

# Wait until you see: ✅ Bootstrap complete at ...
```

---

### Step 5 — Verify Services Are Running

```bash
# SSH into EC2 first
ssh -i ~/.ssh/id_rsa ubuntu@YOUR_EC2_IP

# Check Docker
docker ps
docker version

# Check PostgreSQL
systemctl status postgresql
psql -h localhost -U appuser -d appdb -c "SELECT version();"
# Enter: your db_password from tfvars

# Check CloudWatch Agent
systemctl status amazon-cloudwatch-agent
```

---

### Step 6 — Deploy the Python App

Copy the `app/` folder to EC2 and run it:

```bash
# From your local machine
scp -i ~/.ssh/id_rsa -r app/ ubuntu@YOUR_EC2_IP:~/app

# SSH into EC2
ssh -i ~/.ssh/id_rsa ubuntu@YOUR_EC2_IP

# Set environment variables (match your tfvars)
export DB_NAME=appdb
export DB_USER=appuser
export DB_PASS=Change_Me_Strong_P@ssw0rd!
export AWS_REGION=us-east-1

# Go into app folder and start
cd ~/app
docker compose up -d

# Check it started
docker ps
docker logs myapp-flask
```

---

### Step 7 — Test the Python App

```bash
# On the EC2 itself
curl http://localhost:8080/
curl http://localhost:8080/health
curl http://localhost:8080/users
curl -X POST http://localhost:8080/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Suraj", "email": "suraj@example.com"}'

# From your local machine (replace with your EC2 IP)
curl http://YOUR_EC2_IP:8080/
curl http://YOUR_EC2_IP:8080/users
```

Expected responses:

```json
GET /          → {"message": "Flask app running on EC2!", "status": "ok"}
GET /health    → {"database": "connected", "status": "healthy"}
GET /users     → {"count": 0, "users": []}
POST /users    → {"id": 1, "message": "User created"}
```

---

### Step 8 — View Logs in CloudWatch

#### AWS Console

1. Go to **AWS Console → CloudWatch → Log groups**
2. You'll see:
   - `/ec2/myapp/app` — Flask application logs
   - `/ec2/myapp/docker` — Docker container stdout/stderr
   - `/ec2/myapp/system` — syslog, userdata bootstrap log

#### AWS CLI

```bash
# List log groups
aws logs describe-log-groups --log-group-name-prefix "/ec2/myapp"

# Stream Flask app logs live
aws logs tail /ec2/myapp/app --follow

# Stream Docker container logs live
aws logs tail /ec2/myapp/docker --follow

# Stream system logs live
aws logs tail /ec2/myapp/system --follow
```

---

## Useful Commands on EC2

```bash
# Restart the Flask app container
docker compose -f ~/app/docker-compose.yml restart

# View container logs directly (not CloudWatch)
docker logs myapp-flask -f

# Connect to PostgreSQL
psql -h localhost -U appuser -d appdb

# Check CloudWatch Agent status
systemctl status amazon-cloudwatch-agent

# Restart CloudWatch Agent
sudo systemctl restart amazon-cloudwatch-agent

# Check disk usage
df -h

# Check memory
free -h
```

---

## Teardown — Delete Everything

Run this when you're done to avoid ongoing AWS charges:

```bash
# From tf-ec2-project/ directory on your local machine
terraform destroy
# Type 'yes' when prompted
```

This deletes: EC2, VPC, subnets, security group, IAM role, key pair, and CloudWatch log groups.

> **Note:** CloudWatch log data may persist after destroy. Delete manually in the AWS Console under CloudWatch → Log groups if needed.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| SSH connection refused | Wait 2–3 more minutes; userdata is still running |
| SSH permission denied | Check `ssh_allowed_cidr` in tfvars matches your current IP |
| PostgreSQL auth failed | Verify `db_password` in tfvars matches what you pass to psql |
| Docker app not starting | Run `docker logs myapp-flask` to see the error |
| No logs in CloudWatch | Run `sudo systemctl restart amazon-cloudwatch-agent` on EC2 |
| `terraform apply` IAM error | Ensure your IAM user has `IAMFullAccess` |

---

## Cost Estimate

| Resource | Approx monthly cost |
|---|---|
| t3.medium EC2 | ~$30 |
| 30 GB gp3 EBS | ~$2.50 |
| CloudWatch logs (ingestion) | ~$0.50–$2 depending on volume |
| **Total** | **~$33–$35/month** |

Use `terraform destroy` when not in use to stop charges.
