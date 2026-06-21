# execute.md

End-to-end guide for recording a clean demo of the MinIO + Redis + KEDA + Postgres orchestration platform. Follow phases in order.

---

## Phase 0 — Pre-recording cleanup

Do this **~10 minutes before** hitting record.

### 0.1 — Set the right namespace

```bash
kubectl config set-context --current --namespace=minio-project
kubectl config current-context              # should say minikube
kubectl get pods -n minio-project           # should show minio, redis, postgres, minio-project (no workers — that's correct, they're at 0/0)
```

### 0.2 — Drain leftover tasks from previous experiments

```bash
for q in sort_queue compress_queue convert_queue summarize_queue clean_queue; do
  kubectl exec -n minio-project deployment/redis -- redis-cli del $q
done
kubectl exec -n minio-project deployment/redis -- redis-cli keys 'result:*' | xargs -I{} kubectl exec -n minio-project deployment/redis -- redis-cli del {}
```

### 0.3 — Wipe MinIO bucket

Open MinIO UI → `my-bucket` → select all → Delete. Or via CLI:

```bash
kubectl port-forward -n minio-project svc/minio 9001:9001 &
# Browser: localhost:9001 → my-bucket → delete everything except the modules/ folder
```

### 0.4 — Clear Postgres task history (optional)

Gives clean `SELECT` output on camera.

```bash
kubectl exec -n minio-project deployment/postgres -- psql -U admin -d taskdb -c "TRUNCATE stats;"
```

### 0.5 — Apply the demo slowdown (only if not done already)

Edit `chart/values.yaml`:

```yaml
defaults:
  image: minio-project:latest
  imagePullPolicy: Never
  maxReplicas: 2
  pollingInterval: 15
  cooldownPeriod: 120
```

Then:

```bash
helm upgrade minio-project ./chart -n minio-project
kubectl get scaledobjects -n minio-project   # confirm pollingInterval=15
```

### 0.6 — Confirm workers all at 0/0

```bash
kubectl get deployments -n minio-project | grep worker
```

All five should show `0/0`. If any are non-zero, wait ~2 min for cooldown and re-check.

### 0.7 — Export API URL once for the session

```bash
export API=$(minikube service minio-project -n minio-project --url)
echo $API
cd /home/user/Downloads/Kubernetes_Orchestration-main
```

---

## Phase 1 — Terminal & window layout

Arrange these **before** starting the screen recorder. Recommended: 2×2 grid using a tiling window manager (tmux, terminator, or OS workspaces).

### Layout

```
┌─────────────────────────────┬─────────────────────────────┐
│ Window A: k9s (worker pods) │ Window B: queue depth       │
│                             │                             │
├─────────────────────────────┼─────────────────────────────┤
│ Window C: Browser (Swagger) │ Window D: curl + final SQL  │
│  + Browser (MinIO UI tab)   │                             │
└─────────────────────────────┴─────────────────────────────┘
```

### Window A — k9s

```bash
k9s -n minio-project
```

Once inside:
- Press `1` → confirm namespace `minio-project`
- Press `/` → filter prompt
- Type `worker` → Enter

You'll see an empty pod list filtered to `*worker*`. As workers spin up, they appear in green.

### Window B — Queue depth

```bash
watch -n1 'for q in sort_queue compress_queue convert_queue summarize_queue clean_queue; do
  printf "%-20s %s\n" "$q" "$(kubectl exec -n minio-project deployment/redis -- redis-cli llen $q)"
done'
```

All zeros initially.

### Window C — Browsers

- **Tab 1**: `$API/docs` (Swagger)
- **Tab 2**: `http://localhost:9001` (MinIO UI — credentials `minioadmin / minioadmin`)
  - Pre-launch the port-forward: `kubectl port-forward -n minio-project svc/minio 9001:9001 &`

### Window D — Working terminal

```bash
export API=$(minikube service minio-project -n minio-project --url)
echo $API
cd /home/user/Downloads/Kubernetes_Orchestration-main/csv
```

---

## Phase 2 — Recording flow (~5 minutes total)

Each scene has: **Say** + **Do** + **Show**.

### Scene 1 — Intro & architecture (30s)

**Say:** "This is a FastAPI-based file processing platform. Files live in MinIO, tasks queue through Redis, KEDA scales worker pods on demand based on queue depth, and Postgres tracks every task. Let me show you the live system."

**Do:** Focus on Window A (k9s pods view, no filter for this shot).

**Show:** Five infrastructure pods running (`minio`, `redis`, `postgres`, `minio-project × 2`). Then press `/worker` Enter — empty list. Narrate: "Five worker types exist as deployments but currently at zero replicas. KEDA will spawn them only when there's work."

### Scene 2 — Swagger API tour (30s)

**Say:** "All operations go through this REST API."

**Do:** Switch to Window C → Swagger tab. Scroll through `/upload`, `/task/queue`, `/result`, `/module/list`, `/module/deploy`. Click **`GET /module/list`** → Try it out → Execute.

**Show:** Response listing the five default modules.

### Scene 3 — Upload a file (30s)

**Say:** "First I'll upload a file to MinIO."

**Do:** In Window D:

```bash
curl -X POST "$API/upload/employees.csv" -F "file=@employees.csv"
```

**Show:** `200` response. Switch to Window C → MinIO UI tab → refresh `my-bucket` → point at `employees.csv`.

### Scene 4 — Queue ONE task, watch full lifecycle (60s — the hero shot)

**Say:** "Now I'll queue this file through the sort module. Watch what happens."

**Do:** In Window D:

```bash
curl -X POST "$API/task/queue" \
  -H "Content-Type: application/json" \
  -d '{"file_name":"employees.csv","module":"sort","column":"salary"}'
```

Copy the `task_id` from the response.

**Show:**
- **Window B** — `sort_queue` jumps from `0` → `1` immediately.
- **Window A (k9s)** — wait ~15s. A `sort-worker-xxxxx` pod appears in **yellow** (Pending → ContainerCreating).
- Pod turns **green** (Running).
- **Window B** — `sort_queue` drops back to `0` after ~5s.

**Say while watching:** "KEDA polls the queue every 15 seconds. It saw the task, scaled the deployment from zero to one, Kubernetes scheduled the pod, the pod pulled the queue task and processed it."

### Scene 5 — Verify result (30s)

**Do:** Window C → Swagger tab → **`GET /result/{task_id}`** → Try it out → paste task_id → Execute.

**Show:** Response shows `"status":"success"` and `"result_file":"employees_sorted.csv"`.

**Do:** Switch to MinIO UI → refresh bucket → point at the new sorted file → click → Download to verify it's real.

### Scene 6 — Fan out across 4 modules (60s — the climax)

**Say:** "Now the real value — parallel scaling across multiple modules."

**Do:** In Window D, paste this whole block at once:

```bash
for f in employees customers flights inventory; do
  curl -X POST "$API/upload/${f}.csv" -F "file=@${f}.csv" -s -o /dev/null
done

curl -s -X POST "$API/task/queue" -H "Content-Type: application/json" \
  -d '{"file_name":"employees.csv","module":"sort","column":"salary"}' &
curl -s -X POST "$API/task/queue" -H "Content-Type: application/json" \
  -d '{"file_name":"customers.csv","module":"compress"}' &
curl -s -X POST "$API/task/queue" -H "Content-Type: application/json" \
  -d '{"file_name":"flights.csv","module":"convert","target_format":"parquet"}' &
curl -s -X POST "$API/task/queue" -H "Content-Type: application/json" \
  -d '{"file_name":"inventory.csv","module":"summarize"}' &
wait
echo "4 tasks fanned out"
```

**Show:**
- **Window B** — four queues spike to `1` simultaneously.
- **Window A (k9s)** — within 15s, four different worker pods appear: `sort-worker`, `compress-worker`, `convert-worker`, `summarize-worker`, each going yellow → green in parallel.
- **Window B** — queues drain one-by-one as each worker finishes its task.

**Say:** "Four files, four different processing modules, four parallel workers — all spun from zero on demand."

### Scene 7 — Show task history in Postgres (30s)

**Say:** "Every task is recorded with its worker pod, status, and result."

**Do:** In Window D:

```bash
kubectl exec -n minio-project deployment/postgres -- psql -U admin -d taskdb \
  -c "SELECT substring(task_id::text,1,8) AS id, module, status, result_file, worker_pod FROM stats ORDER BY started_at DESC LIMIT 10;"
```

**Show:** Clean table with the 5 tasks you just ran. Point at the `worker_pod` column — different pods for different tasks.

### Scene 8 — Outro: scale-down (30s)

**Say:** "And when work is done, workers scale back to zero. Idle cost: zero."

**Do:** Switch to Window A (k9s). Wait ~2 minutes (or fast-forward in editing). Workers transition green → orange (Terminating) → disappear.

**Show:** Pod list empty again. Run in Window D as final confirmation:

```bash
kubectl get pods -n minio-project | grep worker   # empty
```

---

## Phase 3 — Reset between takes

If you mess up and need to re-shoot, run this to reset the world:

```bash
# Drain queues
for q in sort_queue compress_queue convert_queue summarize_queue clean_queue; do
  kubectl exec -n minio-project deployment/redis -- redis-cli del $q
done

# Wipe results
kubectl exec -n minio-project deployment/redis -- redis-cli keys 'result:*' | xargs -I{} kubectl exec -n minio-project deployment/redis -- redis-cli del {} 2>/dev/null

# Clear Postgres history
kubectl exec -n minio-project deployment/postgres -- psql -U admin -d taskdb -c "TRUNCATE stats;"

# Force workers back to zero
kubectl scale -n minio-project \
  deployment/sort-worker \
  deployment/compress-worker \
  deployment/convert-worker \
  deployment/summarize-worker \
  deployment/clean-worker \
  --replicas=0

# Re-empty the bucket (manual in MinIO UI is faster than CLI here)
```

Wait 30s, confirm:

```bash
kubectl get pods -n minio-project | grep worker   # empty
```

Then you're ready for take 2.

---

## Pre-flight checklist

Run this right before hitting record. If all five pass, you're cleared.

```bash
# 1. Namespace
kubectl config current-context

# 2. API reachable
curl -s $API/module/list | python3 -m json.tool | head -5

# 3. Workers at 0/0
kubectl get deployments -n minio-project | grep worker

# 4. Queues empty
for q in sort_queue compress_queue convert_queue summarize_queue clean_queue; do
  echo -n "$q: "; kubectl exec -n minio-project deployment/redis -- redis-cli llen $q
done

# 5. Scalers healthy
kubectl get scaledobjects -n minio-project
```

---

## Pacing & narration tips

- **Pause ~5s after each `curl`** before cutting to k9s. Gives KEDA time to react and lets viewers' eyes catch up.
- **Zoom your terminal font** to 16pt minimum. Viewers can't read 11pt on a phone.
- **Use a short prompt** for the recording — e.g. `PS1='$ '` — so paths don't dominate the frame.
- **Edit out the cooldown gap** in Scene 8. Two minutes of waiting is dead air; freeze-frame or fast-forward.

Total runtime after editing: **~4 minutes**.