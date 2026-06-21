import os
import time
import redis
import json
import importlib
import socket
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import db
import file_ops

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

QUEUE_NAME = os.getenv("QUEUE_NAME", "sort_queue")
MODULE_NAME = os.getenv("MODULE_NAME", "sort")
WORKER_POD = socket.gethostname()
IN_PROGRESS_KEY = f"in_progress:{QUEUE_NAME}"
TASK_TIMEOUT_SECONDS = 300

# ── Download module from MinIO if not found locally ──────────
module_path = f"modules/{MODULE_NAME}_module.py"
if not os.path.exists(module_path):
    try:
        print(f"Module {MODULE_NAME} not found locally, downloading from MinIO...")
        os.makedirs("modules", exist_ok=True)
        data = file_ops.download_file(f"modules/{MODULE_NAME}_module.py")
        with open(module_path, "wb") as f:
            f.write(data)
        print(f"Module {MODULE_NAME} downloaded successfully")
    except Exception as e:
        print(f"Failed to download module from MinIO: {e}")

mod = importlib.import_module(f"modules.{MODULE_NAME}_module")
process_fn = mod.process

db.init_db()

# ── Checkpoint: Recover stuck/crashed tasks on startup ───────
def recover_stuck_tasks():
    print("Checking for stuck/crashed tasks...")
    stuck_tasks = r.hgetall(IN_PROGRESS_KEY)
    now = datetime.utcnow()
    recovered = 0
    for task_id, task_json in stuck_tasks.items():
        try:
            task = json.loads(task_json)
            started_at = datetime.fromisoformat(task.get("started_at", now.isoformat()))
            elapsed = (now - started_at).total_seconds()
            if elapsed > TASK_TIMEOUT_SECONDS:
                print(f"Recovering crashed task: {task_id} (stuck for {int(elapsed)}s)")
                r.rpush(QUEUE_NAME, json.dumps(task))
                r.hdel(IN_PROGRESS_KEY, task_id)
                db.save_stat(
                    task_id=task_id,
                    file_name=task.get("file_name"),
                    module=MODULE_NAME,
                    queue_name=QUEUE_NAME,
                    status="retried",
                    error="Worker crashed — task re-queued on restart",
                    result_file=None,
                    started_at=started_at,
                    finished_at=now,
                    worker_pod=WORKER_POD
                )
                recovered += 1
        except Exception as e:
            print(f"Error recovering task {task_id}: {e}")
    print(f"Recovered {recovered} stuck tasks")

# ── Helper: extract result file from any module's result ─────
def get_result_file(result: dict, fallback: str) -> str:
    return (
        result.get("result_file") or
        result.get("sorted_file") or
        result.get("compressed_file") or
        result.get("converted_file") or
        result.get("summary_file") or
        result.get("cleaned_file") or
        result.get("normalized_file") or
        result.get("filtered_file") or
        fallback
    )

# ── Send email notification ───────────────────────────────────
def send_email(workflow: dict):
    try:
        steps_summary = "\n".join([
            f"  {module}: {step['status']} ({step.get('output_file', 'N/A')})"
            for module, step in workflow["steps"].items()
        ])
        msg = MIMEText(f"""
Your workflow has completed successfully!

Workflow ID : {workflow['workflow_id']}
File        : {workflow['file_name']}

Steps:
{steps_summary}

You can download your processed file from MinIO.
        """)
        msg["Subject"] = f"Workflow {workflow['workflow_id']} Completed"
        msg["From"] = os.getenv("SMTP_FROM", "noreply@minio-project.com")
        msg["To"] = workflow["notify_email"]

        with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), 587) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            server.sendmail(msg["From"], workflow["notify_email"], msg.as_string())
        print(f"Email sent to {workflow['notify_email']}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ── DAG: Check and queue steps whose dependencies are all met ─
def resolve_dag(workflow: dict, just_completed_module: str):
    steps = workflow["steps"]
    now = datetime.utcnow().isoformat()
    queue_map_stored = r.get("queue_map")
    queue_map = json.loads(queue_map_stored) if queue_map_stored else {}

    for module_name, step in steps.items():
        # Only check pending steps
        if step["status"] != "pending":
            continue

        # Check if all dependencies are completed
        deps = step["depends_on"]
        all_deps_done = all(steps[dep]["status"] == "completed" for dep in deps if dep in steps)

        if not all_deps_done:
            continue

        # ── All dependencies met — determine input file ──
        # If multiple dependencies, use the output of the last completed dependency
        # or the root file if no dependencies
        if len(deps) == 0:
            input_file = workflow["file_name"]
        else:
            # Use output file of the dependency that just completed
            # For multiple deps, prefer the one that just completed
            if just_completed_module in deps:
                input_file = steps[just_completed_module]["output_file"]
            else:
                # Use the last dependency's output
                input_file = steps[deps[-1]]["output_file"]

        # Mark as in_progress
        step["status"] = "in_progress"
        step["input_files"][just_completed_module] = input_file
        step["started_at"] = now
        steps[module_name] = step
        workflow["steps"] = steps
        r.set(f"workflow:{workflow['workflow_id']}", json.dumps(workflow))

        # Queue the task
        next_queue = queue_map.get(module_name, f"{module_name}_queue")
        next_task = {
            "task_id": str(__import__("uuid").uuid4()),
            "file_name": input_file,
            "module": module_name,
            "column": step.get("column"),
            "algorithm": step.get("algorithm", "quick"),
            "target_format": step.get("target_format"),
            "workflow_id": workflow["workflow_id"],
            "step_module": module_name
        }
        r.rpush(next_queue, json.dumps(next_task))
        print(f"Workflow {workflow['workflow_id']} — queued {module_name} (deps met: {deps}) with input {input_file}")

# ── Workflow: Handle step completion ─────────────────────────
def handle_workflow(task: dict, result: dict):
    workflow_id = task.get("workflow_id")
    if not workflow_id:
        return

    workflow_json = r.get(f"workflow:{workflow_id}")
    if not workflow_json:
        return

    workflow = json.loads(workflow_json)
    step_module = task.get("step_module", MODULE_NAME)
    steps = workflow["steps"]
    now = datetime.utcnow().isoformat()

    # ── Mark current step as completed ──
    output_file = get_result_file(result, task["file_name"])
    steps[step_module]["status"] = "completed"
    steps[step_module]["output_file"] = output_file
    steps[step_module]["finished_at"] = now
    workflow["steps"] = steps
    r.set(f"workflow:{workflow_id}", json.dumps(workflow))

    print(f"Workflow {workflow_id} — {step_module} completed → output: {output_file}")

    # ── Check if all steps are done ──
    all_done = all(s["status"] == "completed" for s in steps.values())
    if all_done:
        workflow["status"] = "completed"
        r.set(f"workflow:{workflow_id}", json.dumps(workflow))
        print(f"Workflow {workflow_id} ALL steps completed — sending email")
        send_email(workflow)
        return

    # ── Resolve DAG — queue any steps whose deps are now met ──
    resolve_dag(workflow, step_module)

# ── Workflow: Mark step and workflow as failed ────────────────
def handle_workflow_failure(task: dict, error: str):
    workflow_id = task.get("workflow_id")
    if not workflow_id:
        return

    workflow_json = r.get(f"workflow:{workflow_id}")
    if not workflow_json:
        return

    workflow = json.loads(workflow_json)
    step_module = task.get("step_module", MODULE_NAME)
    steps = workflow["steps"]
    now = datetime.utcnow().isoformat()

    steps[step_module]["status"] = "failed"
    steps[step_module]["error"] = error
    steps[step_module]["finished_at"] = now

    workflow["status"] = "failed"
    workflow["steps"] = steps
    r.set(f"workflow:{workflow_id}", json.dumps(workflow))
    print(f"Workflow {workflow_id} failed at {step_module}: {error}")

# Run recovery on every worker startup
recover_stuck_tasks()

print(f"Worker started — queue: {QUEUE_NAME}, module: {MODULE_NAME}, pod: {WORKER_POD}")

while True:
    _, task_json = r.blpop(QUEUE_NAME)
    task = json.loads(task_json)
    task_id = task["task_id"]
    started_at = datetime.utcnow()
    finished_at = None

    # ── Checkpoint: mark task as in-progress BEFORE processing ──
    task["started_at"] = started_at.isoformat()
    task["worker_pod"] = WORKER_POD
    r.hset(IN_PROGRESS_KEY, task_id, json.dumps(task))

    print(f"Picked up: {task.get('file_name')} | task_id: {task_id} | module: {task.get('step_module', MODULE_NAME)}")
    time.sleep(15)

    try:
        result = process_fn(task)
        result["file_name"] = task.get("file_name")
        finished_at = datetime.utcnow()
        db.save_stat(
            task_id=task_id,
            file_name=task.get("file_name"),
            module=MODULE_NAME,
            queue_name=QUEUE_NAME,
            status="success",
            error=None,
            result_file=get_result_file(result, task.get("file_name")),
            started_at=started_at,
            finished_at=finished_at,
            worker_pod=WORKER_POD
        )
        handle_workflow(task, result)

    except Exception as e:
        finished_at = finished_at or datetime.utcnow()
        result = {"file_name": task.get("file_name"), "error": str(e), "status": "failed"}
        db.save_stat(
            task_id=task_id,
            file_name=task.get("file_name"),
            module=MODULE_NAME,
            queue_name=QUEUE_NAME,
            status="failed",
            error=str(e),
            result_file=None,
            started_at=started_at,
            finished_at=finished_at,
            worker_pod=WORKER_POD
        )
        handle_workflow_failure(task, str(e))

    finally:
        r.hdel(IN_PROGRESS_KEY, task_id)

    r.set(f"result:{task_id}", json.dumps(result), ex=3600)
    print(f"Done: {result}")