# ORBIT Production Deployment Guide (AWS EC2)

This guide details the step-by-step process for deploying the ORBIT Tactical Fusion & Decision Support system to an **AWS EC2 `t3.small`** instance (2 GiB RAM, 2 vCPU, 4 GiB swap, Ubuntu 24.04 LTS, `linux/amd64`) in the **ap-south-1** (Mumbai) region, with automatic HTTPS via **Caddy** and **DuckDNS**.

All Docker container images are built automatically in GitHub Actions CI and pushed to **GitHub Container Registry (GHCR)**. The EC2 instance does not build images or models locally; it only pulls pre-built images and runs them.

---

## 1. Prerequisites & AWS Infrastructure Setup

### 1.1 EC2 Instance Launch
1. **AMI**: Ubuntu Server 24.04 LTS (`x86_64` / `amd64`).
2. **Instance Type**: `t3.small` (2 vCPUs, 2 GiB RAM).
3. **Storage**: 20 GiB gp3 root volume.
4. **Elastic IP**: Allocate an Elastic IP and associate it with your EC2 instance so the public IP remains static.

### 1.2 Security Group Rules
Configure your EC2 Security Group inbound rules:
| Type | Port | Protocol | Source | Purpose |
|---|---|---|---|---|
| SSH | 22 | TCP | Your IP / Operator CIDR | Remote Administration |
| HTTP | 80 | TCP | `0.0.0.0/0` | ACME HTTP-01 challenge & HTTPS redirect |
| HTTPS | 443 | TCP | `0.0.0.0/0` | Production Browser & WebSocket Access |

> **Note**: Do **not** open port 8000 on the host security group. The FastAPI backend is exposed only within the internal Docker network and proxied by Caddy.

### 1.3 DuckDNS Configuration
1. Log in to [DuckDNS](https://www.duckdns.org).
2. Create or select a domain (e.g. `orbit-hq.duckdns.org`).
3. Set the IP address of your domain to your EC2 instance's Elastic IP.
4. Verify DNS propagation:
   ```bash
   dig +short orbit-hq.duckdns.org
   ```

---

## 2. Server Preparation (Swap & Docker)

SSH into your Ubuntu 24.04 instance:
```bash
ssh -i /path/to/key.pem ubuntu@<EC2_PUBLIC_IP>
```

### 2.1 Configure 4 GiB Swap Space
Because `t3.small` has 2 GiB of physical memory, allocating 4 GiB of swap space is mandatory to prevent out-of-memory errors during high sensor load or heavy PCA projections.

Run the following commands on the EC2 host:
```bash
# Check existing swap (typically 0 on AWS default images)
sudo swapon --show

# Allocate 4 GiB swap file
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make swap persistent across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Verify memory and swap
free -h
```

### 2.2 Install Docker Engine & Docker Compose
Install the official Docker Engine on Ubuntu 24.04:
```bash
# Update apt index and install prerequisites
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository to apt sources
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker packages
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow ubuntu user to execute docker commands without sudo
sudo usermod -aG docker ubuntu
newgrp docker

# Verify Docker installation
docker --version
docker compose version
```

---

## 3. Deployment Configuration

### 3.1 Clone the Repository
Clone the repository to the EC2 host:
```bash
cd ~
git clone https://github.com/KoroS11/ORBIT.git
cd ORBIT
```

### 3.2 Configure `deploy.env`
Create the production `deploy.env` file from `deploy.env.example`:
```bash
cp deploy.env.example deploy.env
nano deploy.env
```

Populate the required environment variables:
```env
# Domain configuration for automatic Let's Encrypt TLS
DOMAIN=orbit-hq.duckdns.org

# Allowed origins for CORS (matches your HTTPS domain)
ORBIT_ALLOWED_ORIGINS=https://orbit-hq.duckdns.org

# Production LLM provider: gemini | stub | anthropic | ollama
ORBIT_LLM=gemini

# Google Gemini API key and model settings
GEMINI_API_KEY=AIzaSy...your-actual-api-key...
ORBIT_GEMINI_MODEL=gemini-2.5-flash
ORBIT_GEMINI_TIMEOUT_S=60
ORBIT_GEMINI_THINKING_BUDGET=0
ORBIT_LLM_FALLBACK=1

# Concurrency & Memory Controls for t3.small
ORBIT_ATTRIB_WINDOW_S=2.0
ORBIT_LOG_LEVEL=INFO
OMP_NUM_THREADS=2

# Persisted state location inside container
ORBIT_STATE_DIR=/app/data/state
```

> **Security Warning**: `deploy.env` contains API keys and credentials. It is already in `.gitignore` and `.dockerignore`. Never commit `deploy.env` to Git.

### 3.3 Authenticate to GHCR (If Repository / Packages are Private)
If your GHCR package is private, authenticate Docker to GHCR using a GitHub Personal Access Token (PAT):
1. In GitHub, go to **Settings** → **Developer Settings** → **Personal Access Tokens (classic)**.
2. Generate a token with the `read:packages` scope.
3. On the EC2 host, log in to GHCR:
   ```bash
   echo "<YOUR_GITHUB_PAT>" | docker login ghcr.io -u <YOUR_GITHUB_USERNAME> --password-stdin
   ```
*(If your repository and GHCR packages are public, this step is not required).*

---

## 4. Launching the Services

Pull the pre-built images from GHCR and start the containers in detached mode:
```bash
# Pull latest images from GHCR
docker compose pull

# Launch containers in background
docker compose up -d
```

### 4.1 Verify Running Containers
Verify that both containers are running and healthy:
```bash
docker compose ps
```
Expected output:
```
NAME            IMAGE                                   COMMAND                  SERVICE   CREATED          STATUS                    PORTS
orbit-backend   ghcr.io/koros11/orbit-backend:latest    "uvicorn orbit.api:a…"   backend   40 seconds ago   Up 40 seconds (healthy)   8000/tcp
orbit-caddy     ghcr.io/koros11/orbit-frontend:latest   "caddy run --config …"   caddy     20 seconds ago   Up 20 seconds (healthy)   0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
```

### 4.2 Inspect Caddy TLS Certificate Issuance
Check Caddy logs to ensure Let's Encrypt obtained a valid TLS certificate:
```bash
docker compose logs caddy
```
Look for lines indicating successful TLS challenge completion:
```
certificate obtained successfully ... identifier=orbit-hq.duckdns.org
```

### 4.3 Verify Backend Health
Test the backend endpoint through Caddy reverse proxy:
```bash
curl -fsSL https://orbit-hq.duckdns.org/health
```
Expected JSON response:
```json
{
  "status": "ok",
  "provider": "gemini",
  "stub_fallback_active": true,
  "fallback_count": 0,
  "last_fallback_at": null,
  "active_provider": "gemini",
  "llm": "FallbackLLMClient",
  "kb_entries": 16,
  "clients": 0,
  "osint_cluster": {
    "service_attached": true,
    "model_loaded": true,
    "model_name": "all-MiniLM-L6-v2",
    "embedding_dim": 384,
    "window_size": 0,
    "clusters_seen": 0,
    "similarity_threshold": 0.4
  }
}
```

> **Note on Telemetry Semantics**: `stub_fallback_active: true` indicates that the `FallbackLLMClient` wrapper is attached at boot. `fallback_count` tracks the cumulative number of fallback events in this process (with `last_fallback_at` recording the ISO timestamp), and `active_provider` reports which provider answered last (`gemini`, `gemini-cache`, or `stub`).

---

## 5. Browser Testing & Operational Verification

1. Open your browser and navigate to:
   ```
   https://orbit-hq.duckdns.org
   ```
2. **WebSocket Connectivity**: Check the header connection indicator. It should show a green badge indicating connected WebSocket stream (`LIVE`).
3. **Brigade COP**: Observe the multi-domain signal feed, satellite tracks on Cesium globe, and OSINT embedding scatter plot.
4. **Scenario Replay**:
   - In the scenario controls dropdown, select `army_multidomain_attack_chain.jsonl`.
   - Click **Replay**.
   - Verify signals flow into the timeline, anomalies trigger, and attribution narration populates the reasoning panel.
5. **Operator Console**:
   - Switch to the Operator tab via the top navigation bar.
   - Inspect the enriched **System Actions** panel (grouped runs, tool glyphs, duration chips).
   - In the **Decision Rail**, click **Approve** or **Reject** on an authority-flagged decision.
   - Refresh the page to confirm the decision review state persists across page reloads and container restarts.

---

## 6. Operational Management & Runbooks

### 6.1 Checking Live Logs
```bash
# Follow backend logs
docker compose logs -f backend

# Follow caddy logs
docker compose logs -f caddy
```

### 6.2 Monitoring Resource Usage on `t3.small`
Verify that CPU and RAM stay within host limits:
```bash
docker stats
```
The backend container is capped at `1400m` RAM limit via Docker Compose, preventing memory exhaustion.

### 6.3 Switching to `ORBIT_LLM=stub` (Zero-Cost / Offline Mode)
If you wish to run without Gemini API calls or if API quota is reached:
1. Edit `deploy.env`:
   ```bash
   nano deploy.env
   ```
   Set:
   ```env
   ORBIT_LLM=stub
   ```
2. Restart the backend container:
   ```bash
   docker compose restart backend
   ```
3. Verify status:
   ```bash
   curl -fsSL https://orbit-hq.duckdns.org/health
   ```
   The `provider` field will now report `"stub"`.

### 6.4 Redeploying After Git Updates
When new commits are pushed to `main`, GitHub Actions automatically runs tests, builds new images, and publishes them to GHCR.

To deploy the update on EC2:
```bash
cd ~/ORBIT
git pull
docker compose pull
docker compose up -d
```
Docker will recreate only the updated containers with zero unnecessary downtime.
State stored in the `backend_state` and `caddy_data` named volumes will be preserved.
