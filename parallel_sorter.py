from concurrent.futures import ThreadPoolExecutor
import file_ops

def sort_single(task):
    try:
        sorted_name = file_ops.sort_and_store(
            task["file_name"],
            task["column"],
            task.get("algorithm", "quick")
        )
        return {"file_name": task["file_name"], "sorted_file": sorted_name, "status": "success"}
    except Exception as e:
        return {"file_name": task["file_name"], "error": str(e), "status": "failed"}

def sort_files_parallel(tasks, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(sort_single, tasks))
    return results
