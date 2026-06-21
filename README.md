# MinIO Project — Complete Setup & Usage Guide

## Overview

This project is a FastAPI-based file processing system that:
- Stores files in **MinIO** (object storage)
- Uses **Redis** as a queue/message broker
- Uses **Kubernetes + KEDA** to auto-scale workers based on queue length
- Allows you to upload custom processing modules (normalize, clean, filter, etc.) and run them on files
- Tracks all task results in **PostgreSQL**

---

## Project Structure

```
MINIO_PROJECT/
├── chart/                        # Helm chart for Kubernetes deployment
│   ├── templates/
│   │   ├── configmap.yaml        # Environment variables for workers
│   │   ├── deployment.yaml       # Main FastAPI app deployment
│   │   ├── minio-deployment.yaml # MinIO deployment
│   │   ├── postgres-deployment.yaml
│   │   ├── redis-deployment.yaml
│   │   ├── secret.yaml           # Credentials
│   │   ├── service.yaml          # Service definitions
│   │   └── workers.yaml          # Worker deployments + KEDA scalers
│   ├── Chart.yaml
│   └── values.yaml               # Configurable values
├── modules/                      # Processing modules
│   ├── __init__.py
│   ├── compress_module.py
│   ├── convert_module.py
│   ├── sort_module.py
│   └── summarize_module.py
├── db.py                         # PostgreSQL task tracking
├── Dockerfile
├── file_ops.py                   # MinIO file operations
├── main.py                       # FastAPI app + all endpoints
├── minio_client.py               # MinIO connection
├── parallel_sorter.py            # Multi-file parallel sorting
├── queue_worker.py               # Worker that processes queue tasks
├── requirements.txt
└── sorter.py                     # Single file sorting logic
```

---

## Prerequisites

Install on the machine:

- **Docker** — build and run images
- **Minikube** — local Kubernetes
- **kubectl** — Kubernetes CLI
- **Helm** — deploy the chart
- **Python 3.12+** — optional, for local scripts

---

## Setup from scratch (summary)

On a clean system, in order:

1. `minikube start`
2. `eval $(minikube docker-env)` (then in same session: `docker build -t minio-project:latest .`)
3. `kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.11.2/keda-2.11.2.yaml` — wait for `kubectl get pods -n keda` to show ready
4. If you have leftover resources from an earlier install, run the [clean-slate steps](#troubleshooting) in Troubleshooting, then:
   `helm install minio-project ./chart`
5. `kubectl exec -it deployment/redis -- redis-cli config set appendonly yes` (once)
6. Verify: `kubectl get pods` (all Running)
7. API URL: `minikube service minio-project --url`. To expose API/docs on the network: `kubectl port-forward service/minio-project 9080:80 --address 0.0.0.0` (or run with `nohup ... &` to keep in background; use another host port if 9080 is in use).

---

## Naming Conventions

### Module Files
- Must be named `<module_name>_module.py`
- Example: `normalize_module.py`, `clean_module.py`, `filter_module.py`
- Must contain a `process(task: dict) -> dict` function

### Queue Names
- Automatically derived from module name: `<module_name>_queue`
- Example: `normalize` → `normalize_queue`, `clean` → `clean_queue`
- You never need to manually name queues — they are auto-created on registration

### Worker Deployments
- Named `<module_name>-worker`
- Example: `normalize-worker`, `clean-worker`

### KEDA Scalers
- Named `<module_name>-worker-scaler`
- Example: `normalize-worker-scaler`

---

## Queue Map Logic

The queue map is a key-value store that maps module names to their Redis queues:

```json
{
  "normalize": "normalize_queue",
  "clean": "clean_queue",
  "filter": "filter_queue"
}
```

### How it works:
1. Queue map is **stored in Redis** so it survives pod restarts
2. When you register a module via `/module/register` or `/module/deploy`, it automatically adds `module_name → module_queue` to the map and saves to Redis
3. When `/task/queue` is called, it **reads fresh from Redis every time** to get the correct queue — this prevents stale routing issues
4. If Redis is empty (first time or reset), it falls back to `DEFAULT_QUEUE_MAP` in `main.py`

### Default modules (always available):
```python
DEFAULT_QUEUE_MAP = {
    "sort": "sort_queue",
    "compress": "compress_queue",
    "convert": "convert_queue",
    "summarize": "summarize_queue",
    "clean": "clean_queue"
}
```

Any new module you register gets added to this map automatically.

---

## 1. Start Minikube

```bash
minikube start
```

---

## 2. Point Docker to Minikube's Daemon

This ensures images built locally are available inside minikube — **run this in every new terminal session**:

```bash
eval $(minikube docker-env)
```

---

## 3. Build the Docker Image

```bash
docker build -t minio-project:latest .
```

> Use `--no-cache` if you want a clean build:
> ```bash
> docker build --no-cache -t minio-project:latest .
> ```

---

## 4. Install KEDA (First Time Only)

```bash
kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.11.2/keda-2.11.2.yaml
```

Wait for KEDA to be ready:
```bash
kubectl get pods -n keda
```

Do not apply CRD manifests (e.g. ScaledJob) separately — the main manifest includes required CRDs; separate apply can cause server-side-apply conflicts.

---

## 5. Deploy Everything with Helm

If you had a previous install or see "exists and cannot be imported" (invalid ownership), run the [clean-slate steps](#troubleshooting) in Troubleshooting first.

```bash
helm install minio-project ./chart
```

If already installed, upgrade:
```bash
helm upgrade minio-project ./chart
```

This deploys: MinIO, Redis, PostgreSQL, the main FastAPI app, ServiceAccount, and worker deployments with KEDA scalers.

> Do not use `kubectl apply -f k8s/` — all manifests are in `chart/templates/`.

---

## 6. Enable Redis Persistence (First Time Only)

Run this once so the queue map survives Redis restarts:

```bash
kubectl exec -it deployment/redis -- redis-cli config set appendonly yes
```

---

## 7. Verify All Pods Are Running

```bash
kubectl get pods
kubectl get scaledobjects
```

All pods should show `Running` status.

---

## 8. Get the API URL

```bash
minikube service minio-project --url
```

This will print the URL directly. Example output:
```
http://192.168.49.2:30743
```

> Use this URL for all API calls in the steps below.

**Expose API/docs to network (other machines on LAN):** Forward only the main app (MinIO and Postgres stay localhost-only):
```bash
kubectl port-forward service/minio-project 9080:80 --address 0.0.0.0
```
From other machines: `http://<this-machine-IP>:9080` and `http://<this-machine-IP>:9080/docs`. Use a different host port (e.g. 9081) if 9080 is in use.

To run in background (survives closing terminal):
```bash
nohup kubectl port-forward service/minio-project 9080:80 --address 0.0.0.0 > /tmp/pf.log 2>&1 &
```
Stop later: `pkill -f "port-forward service/minio-project"`.
---

## 9. Access the API Docs

Replace `<url>` with the URL from the previous step:

```
http://<url>/docs
```

Example:
```
http://192.168.49.2:30743/docs
```

---

## 10. Access MinIO UI

```bash
kubectl port-forward service/minio 9001:9001
```

Open `http://localhost:9001`
- Username: `minioadmin`
- Password: `minioadmin`

---

## 11. Access PostgreSQL (Navicat or any DB client)

```bash
kubectl port-forward service/postgres 5433:5432
```

Connect via:
- Host: `localhost`
- Port: `5433`
- Username: `admin`
- Password: `admin123`
- Database: `taskdb`

---

## 12. Upload Files to MinIO

Single file:
```bash
curl -X POST "http://192.168.49.2:30743/upload/myfile.csv" \
  -F "file=@myfile.csv"
```

Upload 15 files at once:
```bash
for f in cricketers customers data employees flights hospitals inventory movies orders products students startups restaurants vehicles schools; do
  curl -X POST "http://192.168.49.2:30743/upload/${f}.csv" \
    -F "file=@${f}.csv"
done
```

---

## 13. Adding a Processing Module

### Option A: Deploy in One Step (Recommended)

Upload and register in a single API call:

```bash
curl -X POST "http://192.168.49.2:30743/module/deploy" \
  -F "file=@normalize_module.py"
```

This automatically:
- Uploads the module file to MinIO
- Validates it has a `process()` function
- Creates a Kubernetes worker deployment
- Creates a KEDA autoscaler
- Maps `normalize` → `normalize_queue` in Redis

### Option B: Two Steps (Upload then Register)

**Step 1 — Upload:**
```bash
curl -X POST "http://192.168.49.2:30743/module/upload" \
  -F "file=@normalize_module.py"
```

**Step 2 — Register:**
```bash
curl -X POST "http://192.168.49.2:30743/module/register" \
  -H "Content-Type: application/json" \
  -d '{"module_name": "normalize"}'
```

---

## 14. Verify Module is Registered

```bash
curl http://192.168.49.2:30743/module/list
```

Expected response:
```json
{
  "registered_modules": ["sort", "clean", "normalize"],
  "queues": {
    "sort": "sort_queue",
    "clean": "clean_queue",
    "normalize": "normalize_queue"
  }
}
```

---

## 15. Queue Files for Processing

### Single file:
```bash
curl -X POST "http://192.168.49.2:30743/task/queue" \
  -H "Content-Type: application/json" \
  -d '{"file_name": "myfile.csv", "module": "normalize"}'
```

### Multiple files — single module:
```bash
FILES="cricketers.csv customers.csv data.csv employees.csv flights.csv hospitals.csv inventory.csv movies.csv orders.csv products.csv students.csv startups.csv restaurants.csv vehicles.csv schools.csv"
for f in $FILES; do
  curl -X POST "http://192.168.49.2:30743/task/queue" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\":\"$f\",\"module\":\"normalize\"}" &
done
wait
```

### Multiple files — multiple modules at once:
```bash
FILES="cricketers.csv customers.csv data.csv employees.csv flights.csv hospitals.csv inventory.csv movies.csv orders.csv products.csv students.csv startups.csv restaurants.csv vehicles.csv schools.csv"
for f in $FILES; do
  curl -X POST "http://192.168.49.2:30743/task/queue" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\":\"$f\",\"column\":\"salary\",\"module\":\"sort\"}" &
  curl -X POST "http://192.168.49.2:30743/task/queue" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\":\"$f\",\"module\":\"compress\"}" &
  curl -X POST "http://192.168.49.2:30743/task/queue" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\":\"$f\",\"module\":\"convert\",\"target_format\":\"parquet\"}" &
  curl -X POST "http://192.168.49.2:30743/task/queue" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\":\"$f\",\"module\":\"summarize\"}" &
done
wait
```

---

## 16. Check Task Result

```bash
curl http://192.168.49.2:30743/result/<task_id>
```

Response when pending:
```json
{"status": "pending", "task_id": "..."}
```

Response when done:
```json
{"status": "success", "file_name": "myfile.csv", "result_file": "myfile_normalized.csv"}
```

---

## 17. Monitor Workers & Queues

Watch pods scale up/down in real time:
```bash
watch -n1 "kubectl get pods | grep worker"
```

Check queue lengths:
```bash
kubectl exec -it deployment/redis -- redis-cli llen sort_queue
kubectl exec -it deployment/redis -- redis-cli llen compress_queue
kubectl exec -it deployment/redis -- redis-cli llen convert_queue
kubectl exec -it deployment/redis -- redis-cli llen summarize_queue
kubectl exec -it deployment/redis -- redis-cli llen normalize_queue
```

---

## Redeploying After Code Changes

Whenever you modify `main.py`, `file_ops.py`, `queue_worker.py`, or any other file:

```bash
eval $(minikube docker-env)
docker build -t minio-project:latest .
kubectl rollout restart deployment/minio-project
kubectl rollout status deployment/minio-project
```

---

## Writing a Custom Module

Every module must:
1. Be named `<module_name>_module.py`
2. Have a `process(task: dict) -> dict` function
3. Return a dict with at least `status` and `result_file`

Example:
```python
import pandas as pd
import io
import file_ops

def process(task: dict) -> dict:
    file_name = task.get("file_name")

    # Download file from MinIO
    data = file_ops.download_file(file_name)
    df = pd.read_csv(io.BytesIO(data))

    # Your processing logic here
    df = df.drop_duplicates()

    # Upload result back to MinIO
    output = io.BytesIO()
    df.to_csv(output, index=False)
    result_name = file_name.replace(".csv", "_processed.csv")
    file_ops.upload_file(result_name, output.getvalue())

    return {"status": "success", "result_file": result_name}
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload/{file_name}` | Upload a file to MinIO |
| GET | `/download/{file_name}` | Download a file from MinIO |
| GET | `/download/{file_name}/zip` | Download a file as zip |
| POST | `/download/zip/bundle` | Download multiple files as zip |
| POST | `/sort` | Sort a list in memory |
| POST | `/sort/file` | Sort a file by column |
| POST | `/sort/parallel` | Sort multiple files in parallel |
| POST | `/module/deploy` | Upload + register a module in one step |
| POST | `/module/upload` | Upload a module file only |
| POST | `/module/register` | Register an already uploaded module |
| GET | `/module/list` | List all registered modules and queues |
| DELETE | `/module/{module_name}` | Delete a module and its worker |
| POST | `/task/queue` | Queue a file for processing |
| GET | `/result/{task_id}` | Check task result |

---

## Troubleshooting

**Secret/Service/other resource "exists and cannot be imported" (invalid ownership metadata):**

Leftover resources from a previous non-Helm install (or failed install) conflict with Helm. Clean them and reinstall:

```bash
helm uninstall minio-project 2>/dev/null || true
kubectl delete secret minio-secret -n default --ignore-not-found
kubectl delete svc minio minio-project redis postgres -n default --ignore-not-found
kubectl delete deployment minio minio-project redis postgres -n default --ignore-not-found
kubectl delete deployment sort-worker compress-worker summarize-worker convert-worker -n default --ignore-not-found
kubectl delete scaledobject sort-worker-scaler compress-worker-scaler summarize-worker-scaler convert-worker-scaler -n default --ignore-not-found
kubectl delete configmap worker-defaults -n default --ignore-not-found
helm install minio-project ./chart
```

**No running pod for service minio-project / SVC_UNREACHABLE:**
Ensure the image is built inside Minikube (not just on host Docker) and that the app pod is running:
```bash
eval $(minikube docker-env)
docker build -t minio-project:latest .
kubectl rollout restart deployment/minio-project
kubectl get pods -l app=minio-project
kubectl describe pod -l app=minio-project   # if Pending/CrashLoopBackOff
kubectl logs -l app=minio-project           # if crashing
```
If the pod was missing the ServiceAccount, upgrade the chart (adds the SA): `helm upgrade minio-project ./chart`.

**Image not found in minikube:**
```bash
eval $(minikube docker-env)
docker build -t minio-project:latest .
kubectl rollout restart deployment/minio-project
```

**Module not registered after restart:**
```bash
curl http://192.168.49.2:30743/module/list
# If missing, re-deploy the module
curl -X POST "http://192.168.49.2:30743/module/deploy" -F "file=@normalize_module.py"
```

**Tasks going to wrong queue:**
```bash
# Check current queue map
curl http://192.168.49.2:30743/module/list
# Restart to reload Redis queue map
kubectl rollout restart deployment/minio-project
```

**Pod stuck in Terminating:**
```bash
kubectl delete pod <pod-name> --force --grace-period=0
```

**Check pod logs:**
```bash
kubectl logs deployment/minio-project
kubectl logs deployment/normalize-worker
```

**Redis queue map lost after restart:**
```bash
# Re-enable persistence
kubectl exec -it deployment/redis -- redis-cli config set appendonly yes
# Re-register missing modules
curl -X POST "http://192.168.49.2:30743/module/deploy" -F "file=@normalize_module.py"
```