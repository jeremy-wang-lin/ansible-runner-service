# Kubernetes Deployment Guide

This guide covers deploying ansible-runner-service to Kubernetes, including container image strategy, database migrations, and worker autoscaling.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐    │
│  │   Ingress    │────▶│  API Service │────▶│  API Deploy  │    │
│  │  (optional)  │     │   (ClusterIP)│     │  (FastAPI)   │    │
│  └──────────────┘     └──────────────┘     └──────┬───────┘    │
│                                                    │            │
│                              ┌─────────────────────┼────────┐   │
│                              ▼                     ▼        │   │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐│   │
│  │Worker Deploy │────▶│    Redis     │◀────│   MariaDB    ││   │
│  │  (RQ Worker) │     │  (StatefulSet│     │ (StatefulSet)││   │
│  └──────────────┘     │   or managed)│     └──────────────┘│   │
│                       └──────────────┘                      │   │
│                                                             │   │
│  ┌──────────────────────────────────────────────────────────┘   │
│  │ Shared: ConfigMap (git providers), Secret (credentials)      │
│  └──────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

### Components Mapping

| Application Component | Kubernetes Resource |
|----------------------|---------------------|
| FastAPI app | Deployment + Service |
| RQ Worker | Deployment (can scale replicas) |
| Redis | StatefulSet or managed service (AWS ElastiCache, etc.) |
| MariaDB | StatefulSet with PVC or managed service (AWS RDS, etc.) |
| Git credentials | Secret |
| Git providers config | ConfigMap |
| Bundled playbooks/collections | Baked into container image |

---

## Single Image, Multiple Entry Points

This pattern builds one container image that serves multiple roles (API server, worker, migrations) by varying the startup command.

### Why This Pattern?

| Benefit | Explanation |
|---------|-------------|
| **Consistency** | API and worker always run identical code versions - no version drift |
| **Single CI/CD pipeline** | Build once, deploy to multiple deployments |
| **Shared dependencies** | Ansible, Python packages, bundled collections all identical |
| **Smaller registry footprint** | One image tag instead of N separate images |
| **Atomic deployments** | Update all components together by changing one image tag |

### Dockerfile Structure

```dockerfile
FROM python:3.11-slim

# ============================================
# Layer 1: System dependencies (rarely changes)
# ============================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# Layer 2: Python dependencies (changes occasionally)
# ============================================
RUN pip install --no-cache-dir ansible-core

COPY pyproject.toml /app/
RUN pip install --no-cache-dir /app

# ============================================
# Layer 3: Application code (changes frequently)
# ============================================
COPY src/ /app/src/
RUN pip install --no-cache-dir -e /app

# ============================================
# Layer 4: Bundled content (changes per release)
# ============================================
COPY playbooks/ /app/playbooks/
COPY collections/ /app/collections/

# Or install collections from requirements.yml:
# COPY requirements.yml /app/
# RUN ansible-galaxy collection install -r /app/requirements.yml -p /app/collections/

# ============================================
# Runtime configuration
# ============================================
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV ANSIBLE_COLLECTIONS_PATH=/app/collections

# No CMD - specified by Kubernetes
```

### Entry Points

```
┌─────────────────────────────────────────────────────────────────┐
│                    ansible-runner-service:v1.2.3                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  command: ["uvicorn", "ansible_runner_service.main:app", ...]  │
│  └─▶ FastAPI server (HTTP API)                                 │
│                                                                 │
│  command: ["rq", "worker", "--url=redis://..."]                │
│  └─▶ RQ Worker (job processor)                                 │
│                                                                 │
│  command: ["alembic", "upgrade", "head"]                       │
│  └─▶ Database migrations                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Kubernetes Deployments

**API Server Deployment:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ansible-runner-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ansible-runner-api
  template:
    metadata:
      labels:
        app: ansible-runner-api
    spec:
      containers:
      - name: api
        image: myregistry/ansible-runner-service:v1.2.3
        command: ["uvicorn"]
        args:
          - "ansible_runner_service.main:app"
          - "--host=0.0.0.0"
          - "--port=8000"
        ports:
        - containerPort: 8000
        envFrom:
        - secretRef:
            name: ansible-runner-secrets
        - configMapRef:
            name: ansible-runner-config
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

**Worker Deployment:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ansible-runner-worker
spec:
  replicas: 3  # Scale based on job volume (or use KEDA)
  selector:
    matchLabels:
      app: ansible-runner-worker
  template:
    metadata:
      labels:
        app: ansible-runner-worker
    spec:
      terminationGracePeriodSeconds: 600  # Allow 10min for job completion
      containers:
      - name: worker
        image: myregistry/ansible-runner-service:v1.2.3  # Same image!
        command: ["rq"]
        args:
          - "worker"
          - "--url=$(REDIS_URL)"
        envFrom:
        - secretRef:
            name: ansible-runner-secrets
        - configMapRef:
            name: ansible-runner-config
        resources:
          requests:
            memory: "512Mi"   # Workers need more memory (Ansible execution)
            cpu: "200m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
```

**API Service:**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ansible-runner-api
spec:
  selector:
    app: ansible-runner-api
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

### Configuration Resources

**ConfigMap:**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ansible-runner-config
data:
  GIT_PROVIDERS: |
    [
      {"type": "azure", "host": "dev.azure.com", "orgs": ["myorg"], "credential_env": "AZURE_PAT"},
      {"type": "gitlab", "host": "gitlab.company.com", "orgs": ["platform-team"], "credential_env": "GITLAB_TOKEN"}
    ]
```

**Secret:**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: ansible-runner-secrets
type: Opaque
stringData:
  DATABASE_URL: "mysql+pymysql://user:pass@mariadb:3306/ansible_runner"
  REDIS_URL: "redis://redis:6379"
  AZURE_PAT: "your-azure-pat-token"
  GITLAB_TOKEN: "your-gitlab-access-token"
```

### Single Image vs Separate Images

| Aspect | Single Image | Separate Images |
|--------|--------------|-----------------|
| **Version sync** | Guaranteed | Must coordinate |
| **Image size** | Larger (all deps) | Smaller per image |
| **Build time** | One build | Multiple builds |
| **Registry storage** | 1 image × N tags | 3 images × N tags |
| **Debugging** | Same environment everywhere | May differ |
| **Use case** | Most microservices | Large monorepos with distinct components |

**Recommendation:** Use single image for ansible-runner-service because components share code (schemas, models, git_service). Version drift between API and worker would cause bugs.

---

## Database Migration Strategies

There are three main approaches for running database migrations in Kubernetes.

### Strategy Comparison

| Aspect | Init Container | Kubernetes Job | Helm Hook |
|--------|---------------|----------------|-----------|
| **Runs** | Per pod (N times) | Once | Once |
| **Orchestration** | None needed | CI/CD script | Helm |
| **Race condition** | Possible (Alembic handles) | None | None |
| **Failure handling** | Pod won't start | Job retry + CI fails | Helm fails |
| **Visibility** | Pod events | Job status | Helm status |
| **Best for** | Simple setups | CI/CD pipelines | Helm users |

### Recommended: Kubernetes Job

Run migration as a separate Job before updating deployments.

**Migration Job:**

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ansible-runner-migrate-v1-2-3
spec:
  ttlSecondsAfterFinished: 3600  # Cleanup after 1 hour
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
      - name: migrate
        image: myregistry/ansible-runner-service:v1.2.3
        command: ["alembic", "upgrade", "head"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: ansible-runner-secrets
              key: DATABASE_URL
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
```

**Deployment Sequence:**

```
┌─────────────────────────────────────────────────────────────────┐
│ CI/CD Pipeline                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ 1. Build image ──▶ myregistry/ansible-runner-service:v1.2.3    │
│                                                                 │
│ 2. kubectl apply -f migration-job.yaml                         │
│    └─▶ Job starts                                              │
│                                                                 │
│ 3. kubectl wait --for=condition=complete job/migrate-v1-2-3    │
│    └─▶ Wait for migration to finish                            │
│                                                                 │
│ 4. kubectl apply -f deployment.yaml  (only if step 3 succeeds) │
│    └─▶ Rolling update with new image                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**CI/CD Script Example:**

```bash
#!/bin/bash
set -e

IMAGE="myregistry/ansible-runner-service:${VERSION}"

# 1. Apply migration job
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: migrate-${VERSION//\./-}
spec:
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: OnFailure
      containers:
      - name: migrate
        image: ${IMAGE}
        command: ["alembic", "upgrade", "head"]
        envFrom:
        - secretRef:
            name: ansible-runner-secrets
EOF

# 2. Wait for completion (timeout 5 minutes)
kubectl wait --for=condition=complete --timeout=300s job/migrate-${VERSION//\./-}

# 3. Update deployments
kubectl set image deployment/ansible-runner-api api=${IMAGE}
kubectl set image deployment/ansible-runner-worker worker=${IMAGE}

# 4. Wait for rollout
kubectl rollout status deployment/ansible-runner-api
kubectl rollout status deployment/ansible-runner-worker
```

### Alternative: Init Container

For simpler setups, use an init container (runs per pod, but Alembic handles race conditions):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ansible-runner-api
spec:
  template:
    spec:
      initContainers:
      - name: migrate
        image: myregistry/ansible-runner-service:v1.2.3
        command: ["alembic", "upgrade", "head"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: ansible-runner-secrets
              key: DATABASE_URL
      containers:
      - name: api
        # ... main container config
```

### Alternative: Helm Hook

If using Helm, hooks automate the Job-before-Deployment pattern:

```yaml
# templates/migration-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ .Release.Name }}-migrate
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-1"
    "helm.sh/hook-delete-policy": hook-succeeded,before-hook-creation
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
      - name: migrate
        image: {{ .Values.image.repository }}:{{ .Values.image.tag }}
        command: ["alembic", "upgrade", "head"]
        envFrom:
        - secretRef:
            name: {{ .Release.Name }}-secrets
```

### Migration Safety Principles

1. **Forward-only migrations** - Never rely on `alembic downgrade` in production. If a migration is bad, write a new forward migration to fix it.

2. **Backwards-compatible migrations** - During rolling updates, old code runs alongside new code. Migrations must work with both versions (e.g., add columns as nullable first, then backfill, then make NOT NULL in next release).

3. **Test migrations on production data copy** - The migration you tested on empty dev DB may behave differently on production with millions of rows.

---

## Worker Autoscaling with KEDA

KEDA (Kubernetes Event-Driven Autoscaling) scales workers based on Redis queue depth, providing faster response than CPU-based HPA.

### Why KEDA?

| Metric | HPA Behavior | KEDA Behavior |
|--------|--------------|---------------|
| Queue empty | Workers idle at min replicas | Scale to zero (optional) |
| Queue growing | Waits for CPU spike | Scales immediately |
| Burst of jobs | Reactive, delayed | Proactive, fast |
| Cost optimization | Always min replicas | Zero when idle |

### Installation

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace
```

### Basic ScaledObject

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ansible-runner-worker
  namespace: default
spec:
  scaleTargetRef:
    name: ansible-runner-worker          # Deployment name

  minReplicaCount: 1                      # Minimum workers
  maxReplicaCount: 10                     # Maximum workers

  pollingInterval: 15                     # Check queue every 15s
  cooldownPeriod: 300                     # Wait 5min before scaling down

  triggers:
  - type: redis
    metadata:
      address: redis.default.svc.cluster.local:6379
      listName: rq:queue:default          # RQ default queue key
      listLength: "5"                     # Scale up when > 5 jobs
```

### How RQ Stores Jobs in Redis

```
Redis Keys:
┌─────────────────────────────────────────────────────────────────┐
│ rq:queue:default          <- List of job IDs (main queue)       │
│ rq:queue:high             <- High priority queue (if used)      │
│ rq:queue:low              <- Low priority queue (if used)       │
│ rq:job:<job-id>           <- Hash with job details              │
└─────────────────────────────────────────────────────────────────┘
```

KEDA monitors `LLEN rq:queue:default` (list length) to determine scaling.

### Advanced Configuration with Multiple Queues

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ansible-runner-worker
spec:
  scaleTargetRef:
    name: ansible-runner-worker

  minReplicaCount: 1
  maxReplicaCount: 20

  # Multiple triggers - KEDA uses the MAX of all triggers
  triggers:
  # High priority queue - aggressive scaling
  - type: redis
    metadata:
      address: redis:6379
      listName: rq:queue:high
      listLength: "1"                     # Scale on ANY high-priority job

  # Default queue - moderate scaling
  - type: redis
    metadata:
      address: redis:6379
      listName: rq:queue:default
      listLength: "5"                     # Scale when backlog > 5

  # Low priority queue - conservative scaling
  - type: redis
    metadata:
      address: redis:6379
      listName: rq:queue:low
      listLength: "20"                    # Scale when backlog > 20
```

### Scale to Zero Configuration

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ansible-runner-worker
spec:
  scaleTargetRef:
    name: ansible-runner-worker

  minReplicaCount: 0                      # Allow scale to zero!
  maxReplicaCount: 10
  idleReplicaCount: 0                     # Replicas when idle

  triggers:
  - type: redis
    metadata:
      address: redis:6379
      listName: rq:queue:default
      listLength: "1"                     # Scale up on first job
      activationListLength: "0"           # Activate from zero when > 0 jobs
```

### Complete Production Configuration

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ansible-runner-worker
spec:
  scaleTargetRef:
    name: ansible-runner-worker

  minReplicaCount: 1                      # Always have 1 worker ready
  maxReplicaCount: 10                     # Cap at 10 workers

  pollingInterval: 10                     # Check every 10 seconds
  cooldownPeriod: 300                     # 5 min cooldown before scale-down

  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300
          policies:
          - type: Percent
            value: 50                     # Scale down max 50% at a time
            periodSeconds: 60
        scaleUp:
          stabilizationWindowSeconds: 0   # Scale up immediately
          policies:
          - type: Pods
            value: 4                      # Add max 4 pods at a time
            periodSeconds: 15

  triggers:
  - type: redis
    metadata:
      address: redis.default.svc.cluster.local:6379
      listName: rq:queue:default
      listLength: "3"                     # 3 jobs per worker
```

### With Redis Authentication

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ansible-runner-worker
spec:
  scaleTargetRef:
    name: ansible-runner-worker
  triggers:
  - type: redis
    metadata:
      address: redis:6379
      listName: rq:queue:default
      listLength: "5"
    authenticationRef:
      name: redis-auth

---
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: redis-auth
spec:
  secretTargetRef:
  - parameter: password
    name: redis-secrets
    key: redis-password
```

### Scaling Algorithm

KEDA calculates desired replicas using:

```
desiredReplicas = ceil(currentQueueLength / listLength)
```

**Example with `listLength: "5"`:**

| Queue Length | Calculation | Desired Replicas |
|--------------|-------------|------------------|
| 0 | 0 / 5 = 0 | 0 (or min) |
| 3 | 3 / 5 = 0.6 → 1 | 1 |
| 5 | 5 / 5 = 1 | 1 |
| 12 | 12 / 5 = 2.4 → 3 | 3 |
| 47 | 47 / 5 = 9.4 → 10 | 10 (or max) |

**Tuning `listLength`:**

| Value | Behavior | Use Case |
|-------|----------|----------|
| Low (1-3) | Aggressive scaling, fast response | Short jobs, latency-sensitive |
| High (10+) | Conservative scaling, cost-efficient | Long jobs, cost-sensitive |

### Graceful Shutdown

Ansible jobs can run for minutes. Ensure jobs complete when scaling down:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ansible-runner-worker
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 600  # 10 minutes for long playbooks
      containers:
      - name: worker
        # ... config
        lifecycle:
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 5"]
```

**Shutdown flow:**
1. KEDA decides to scale down
2. Kubernetes sends SIGTERM to worker pod
3. RQ worker stops accepting new jobs, continues current job
4. `terminationGracePeriodSeconds` countdown starts
5. Job completes (hopefully within grace period)
6. Worker exits cleanly, pod terminated

**Warning:** If job exceeds grace period, it will be killed mid-execution (SIGKILL).

### Monitoring KEDA

```bash
# Check ScaledObject status
kubectl get scaledobject ansible-runner-worker -o yaml

# See scaling events
kubectl describe scaledobject ansible-runner-worker

# Check HPA created by KEDA
kubectl get hpa

# View KEDA operator logs
kubectl logs -n keda -l app=keda-operator
```

### Recommended Settings Summary

| Configuration | Value | Reasoning |
|---------------|-------|-----------|
| `minReplicaCount` | 1 | Always ready for quick response |
| `maxReplicaCount` | 10-20 | Based on cluster capacity |
| `listLength` | 3-5 | Ansible jobs are medium-length |
| `pollingInterval` | 10-15s | Balance responsiveness vs Redis load |
| `cooldownPeriod` | 300s | Prevent thrashing during variable load |
| `terminationGracePeriodSeconds` | 600s | Allow long playbooks to complete |

---

## Complete Deployment Example

### Directory Structure

```
k8s/
├── base/
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── api-deployment.yaml
│   ├── api-service.yaml
│   ├── worker-deployment.yaml
│   └── worker-scaledobject.yaml
├── overlays/
│   ├── dev/
│   │   ├── kustomization.yaml
│   │   └── secrets.yaml
│   └── prod/
│       ├── kustomization.yaml
│       └── secrets.yaml
└── jobs/
    └── migration-job.yaml
```

### Deployment Commands

```bash
# Development
kubectl apply -k k8s/overlays/dev

# Production
kubectl apply -k k8s/overlays/prod

# Run migration before deployment
kubectl apply -f k8s/jobs/migration-job.yaml
kubectl wait --for=condition=complete job/ansible-runner-migrate --timeout=300s
```

---

## Key Considerations

| Concern | Approach |
|---------|----------|
| **Bundled content updates** | Rebuild image, rolling deployment |
| **Database migrations** | Kubernetes Job before deployment |
| **Worker scaling** | KEDA based on Redis queue length |
| **Git clone temp dirs** | Use emptyDir volume (ephemeral, per-pod) |
| **Ansible execution isolation** | Each worker pod runs jobs sequentially; scale workers for parallelism |
| **Secrets for target hosts** | Mount SSH keys or use Ansible Vault with external secret management |
| **Managed vs self-hosted DBs** | Prefer managed Redis/MariaDB for production (less operational overhead) |
