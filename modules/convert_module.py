import file_ops
import pandas as pd
import io

MODULE_NAME = "convert"

def process(task: dict) -> dict:
    data = file_ops.download_file(task["file_name"])
    df = pd.read_csv(io.BytesIO(data))
    output = io.BytesIO()
    target_format = task.get("target_format", "json")
    if target_format == "json":
        df.to_json(output, orient="records")    # ← moved inside if block
        new_name = task["file_name"].replace(".csv", ".json")
    elif target_format == "parquet":
        df.to_parquet(output, index=False)
        new_name = task["file_name"].replace(".csv", ".parquet")
    file_ops.upload_file(new_name, output.getvalue())
    return {"converted_file": new_name, "status": "success"}