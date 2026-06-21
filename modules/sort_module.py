import file_ops

MODULE_NAME = "sort"

def process(task: dict) -> dict:
    sorted_name = file_ops.sort_and_store(
        task["file_name"],
        task["column"],
        task.get("algorithm", "quick")
    )
    return {"sorted_file": sorted_name, "status": "success"}