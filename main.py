import os
import importlib
import smtplib
from email.mime.text import MIMEText
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Optional
import file_ops
import sorter
import parallel_sorter
import redis
import json
import uuid
from datetime import datetime

app = FastAPI()

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

# ── Queue Map — persisted in Redis so it survives pod restarts ──
DEFAULT_QUEUE_MAP = {
    "sort": "sort_queue",
    "compress": "compress_queue",
    "convert": "convert_queue",
    "summarize": "summarize_queue",
    "clean": "clean_queue"
}

def load_queue_map():
    stored = r.get("queue_map")
    if stored:
        return json.loads(stored)
    return DEFAULT_QUEUE_MAP.copy()

def save_queue_map(queue_map):
    r.set("queue_map", json.dumps(queue_map))

QUEUE_MAP = load_queue_map()

# ── Upload ──────────────────────────────────────────────────
@app.post("/upload/{file_name}")
async def upload(file_name: str, file: UploadFile = File(...)):
    data = await file.read()
    actual_name = file.filename if file.filename else file_name
    msg = file_ops.upload_file(actual_name, data)
    return {"message": msg}

# ── Download ────────────────────────────────────────────────
@app.get("/download/{file_name}")
def download(file_name: str):
    try:
        data = file_ops.download_file(file_name)
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename={file_name}"})
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

# ── Download as Zip ─────────────────────────────────────────
@app.get("/download/{file_name}/zip")
def download_zip(file_name: str):
    try:
        data = file_ops.download_as_zip(file_name)
        zip_name = file_name.rsplit(".", 1)[0] + ".zip"
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename={zip_name}"})
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

# ── Download Multiple as Zip ────────────────────────────────
class ZipRequest(BaseModel):
    files: List[str]
    zip_name: str = "bundle.zip"

@app.post("/download/zip/bundle")
def download_bundle(req: ZipRequest):
    try:
        data = file_ops.download_multiple_as_zip(req.files)
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename={req.zip_name}"})
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

# ── Sort a List ─────────────────────────────────────────────
class SortRequest(BaseModel):
    data: List[float]
    algorithm: str = "quick"

@app.post("/sort")
def sort(req: SortRequest):
    algo = sorter.ALGORITHMS.get(req.algorithm)
    if not algo:
        raise HTTPException(status_code=400, detail=f"Unknown algorithm: {req.algorithm}")
    return {"algorithm": req.algorithm, "sorted": algo(req.data)}

# ── Sort a File ─────────────────────────────────────────────
class FileSortRequest(BaseModel):
    file_name: str
    column: str
    algorithm: str = "quick"

@app.post("/sort/file")
def sort_file(req: FileSortRequest):
    try:
        sorted_name = file_ops.sort_and_store(req.file_name, req.column, req.algorithm)
        return {"message": "Sorted file saved to MinIO", "sorted_file": sorted_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ── Sort Multiple Files in Parallel ────────────────────────
class ParallelSortRequest(BaseModel):
    tasks: List[dict]
    max_workers: int = 4

@app.post("/sort/parallel")
def sort_parallel(req: ParallelSortRequest):
    try:
        results = parallel_sorter.sort_files_parallel(req.tasks, req.max_workers)
        return {
            "total": len(results),
            "successful": len([r for r in results if r["status"] == "success"]),
            "failed": len([r for r in results if r["status"] == "failed"]),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ── Queue a Task ────────────────────────────────────────────
class QueueSortRequest(BaseModel):
    file_name: str
    column: str = None
    algorithm: str = "quick"
    module: str = "sort"
    target_format: str = None

@app.post("/task/queue")
def queue_task(req: QueueSortRequest):
    task_id = str(uuid.uuid4())
    current_queue_map = load_queue_map()
    queue_name = current_queue_map.get(req.module)
    if not queue_name:
        raise HTTPException(status_code=400, detail=f"Module '{req.module}' not registered.")
    task = {
        "task_id": task_id,
        "file_name": req.file_name,
        "column": req.column,
        "algorithm": req.algorithm,
        "module": req.module,
        "target_format": req.target_format
    }
    r.rpush(queue_name, json.dumps(task))
    return {"message": f"Task queued to {queue_name}", "task_id": task_id}

# ── Check Queue Result ──────────────────────────────────────
@app.get("/result/{task_id}")
def get_result(task_id: str):
    result = r.get(f"result:{task_id}")
    if not result:
        return {"status": "pending", "task_id": task_id}
    return json.loads(result)

# ── Workflow Models ──────────────────────────────────────────
class WorkflowStep(BaseModel):
    module: str
    depends_on: List[str] = []
    column: Optional[str] = None
    algorithm: str = "quick"
    target_format: Optional[str] = None

class WorkflowRequest(BaseModel):
    file_name: str
    notify_email: str
    steps: List[WorkflowStep]

# ── Workflow: Run a DAG pipeline ─────────────────────────────
@app.post("/workflow/run")
def run_workflow(req: WorkflowRequest):
    current_queue_map = load_queue_map()

    # Validate all modules are registered
    for step in req.steps:
        if step.module not in current_queue_map:
            raise HTTPException(
                status_code=400,
                detail=f"Module '{step.module}' not registered. Register it first via /module/deploy"
            )

    # Validate depends_on references exist
    module_names = [s.module for s in req.steps]
    for step in req.steps:
        for dep in step.depends_on:
            if dep not in module_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"Module '{step.module}' depends on '{dep}' which is not in the steps list"
                )

    workflow_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # ── Build nested DAG steps structure ──
    steps = {}
    for step in req.steps:
        steps[step.module] = {
            "module": step.module,
            "depends_on": step.depends_on,
            "column": step.column,
            "algorithm": step.algorithm,
            "target_format": step.target_format,
            "status": "pending",
            "input_files": {},     # {dependency_module: output_file}
            "output_file": None,
            "started_at": None,
            "finished_at": None,
            "error": None
        }

    workflow = {
        "workflow_id": workflow_id,
        "file_name": req.file_name,
        "notify_email": req.notify_email,
        "status": "in_progress",
        "created_at": now,
        "steps": steps
    }

    # Save workflow state to Redis
    r.set(f"workflow:{workflow_id}", json.dumps(workflow))

    # ── Queue all steps with no dependencies immediately ──
    queued = 0
    for module_name, step in steps.items():
        if len(step["depends_on"]) == 0:
            step["status"] = "in_progress"
            step["input_files"]["__root__"] = req.file_name
            step["started_at"] = now
            workflow["steps"] = steps
            r.set(f"workflow:{workflow_id}", json.dumps(workflow))

            task_id = str(uuid.uuid4())
            task = {
                "task_id": task_id,
                "file_name": req.file_name,
                "module": module_name,
                "column": step["column"],
                "algorithm": step["algorithm"],
                "target_format": step["target_format"],
                "workflow_id": workflow_id,
                "step_module": module_name
            }
            queue_name = current_queue_map.get(module_name)
            r.rpush(queue_name, json.dumps(task))
            queued += 1

    return {
        "workflow_id": workflow_id,
        "message": f"Workflow started — {queued} initial steps queued",
        "status": "in_progress",
        "steps": steps
    }

# ── Workflow: Check Status ───────────────────────────────────
@app.get("/workflow/status/{workflow_id}")
def workflow_status(workflow_id: str):
    workflow = r.get(f"workflow:{workflow_id}")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return json.loads(workflow)

# ── Workflow: List all workflows ─────────────────────────────
@app.get("/workflow/list")
def list_workflows():
    keys = r.keys("workflow:*")
    workflows = []
    for key in keys:
        workflow = r.get(key)
        if workflow:
            w = json.loads(workflow)
            workflows.append({
                "workflow_id": w["workflow_id"],
                "file_name": w["file_name"],
                "status": w["status"],
                "created_at": w.get("created_at"),
                "modules": list(w["steps"].keys()),
                "steps": w["steps"]
            })
    workflows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"total": len(workflows), "workflows": workflows}

# ── Workflow: Get all workflows a module is part of ──────────
@app.get("/workflow/module/{module_name}")
def workflows_by_module(module_name: str):
    keys = r.keys("workflow:*")
    matched = []
    for key in keys:
        workflow = r.get(key)
        if workflow:
            w = json.loads(workflow)
            if module_name in w["steps"]:
                matched.append({
                    "workflow_id": w["workflow_id"],
                    "file_name": w["file_name"],
                    "workflow_status": w["status"],
                    "created_at": w.get("created_at"),
                    "step": w["steps"][module_name]
                })
    matched.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "module": module_name,
        "total": len(matched),
        "workflows": matched
    }

# ── Deploy a Module ──────────────────────────────────────────
@app.post("/module/deploy")
async def deploy_module(
    file: UploadFile = File(...),
    max_replicas: int = 10,
    queue_name: str = None
):
    if not file.filename.endswith("_module.py"):
        return {"status": "error", "message": "File must end with _module.py"}

    contents = await file.read()

    if b"def process" not in contents:
        return {"status": "error", "message": "Module file must have a process() function"}

    module_name = file.filename.replace("_module.py", "")
    queue = queue_name or f"{module_name}_queue"
    module_path = f"modules/{file.filename}"

    os.makedirs("modules", exist_ok=True)
    with open(module_path, "wb") as f:
        f.write(contents)

    file_ops.upload_file(f"modules/{file.filename}", contents)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"modules.{module_name}_module", module_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "process"):
            return {"status": "error", "message": f"{module_name}_module.py has no process() function"}
    except Exception as e:
        return {"status": "error", "message": f"Module validation failed: {str(e)}"}

    deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {module_name}-worker
spec:
  selector:
    matchLabels:
      app: {module_name}-worker
  template:
    metadata:
      labels:
        app: {module_name}-worker
    spec:
      serviceAccountName: minio-project-sa
      containers:
        - name: {module_name}-worker
          image: minio-project:latest
          imagePullPolicy: Never
          command: ["python", "-u", "queue_worker.py"]
          envFrom:
            - configMapRef:
                name: worker-defaults
          env:
            - name: QUEUE_NAME
              value: "{queue}"
            - name: MODULE_NAME
              value: "{module_name}"
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {module_name}-worker-scaler
spec:
  scaleTargetRef:
    name: {module_name}-worker
  minReplicaCount: 0
  maxReplicaCount: {max_replicas}
  pollingInterval: 5
  cooldownPeriod: 30
  triggers:
    - type: redis
      metadata:
        address: redis.default.svc.cluster.local:6379
        listName: {queue}
        listLength: "1"
"""
    yaml_path = f"/tmp/{module_name}-worker.yaml"
    with open(yaml_path, "w") as f:
        f.write(deployment_yaml)

    result = os.system(f"kubectl apply -f {yaml_path}")
    if result != 0:
        return {"status": "error", "message": "kubectl apply failed"}

    QUEUE_MAP[module_name] = queue
    save_queue_map(QUEUE_MAP)

    return {
        "status": "success",
        "message": f"Module '{module_name}' deployed successfully",
        "queue": queue,
        "max_replicas": max_replicas
    }

# ── List All Modules ─────────────────────────────────────────
@app.get("/module/list")
def list_modules():
    return {
        "registered_modules": list(QUEUE_MAP.keys()),
        "queues": QUEUE_MAP
    }

# ── Delete a Module ──────────────────────────────────────────
@app.delete("/module/{module_name}")
def delete_module(module_name: str):
    if module_name not in QUEUE_MAP:
        return {"status": "error", "message": f"Module '{module_name}' not found"}
    os.system(f"kubectl delete deployment {module_name}-worker")
    os.system(f"kubectl delete scaledobject {module_name}-worker-scaler")
    del QUEUE_MAP[module_name]
    save_queue_map(QUEUE_MAP)
    return {"status": "success", "message": f"Module '{module_name}' deleted"}