import file_ops
import pandas as pd
import io
import json

MODULE_NAME = "summarize"

def process(task: dict) -> dict:
    data = file_ops.download_file(task["file_name"])
    df = pd.read_csv(io.BytesIO(data))
    summary = {}
    for col in df.columns:
        if df[col].dtype in ["int64", "float64"]:
            summary[col] = {
                "type": "numeric",
                "count": int(df[col].count()),
                "mean": round(float(df[col].mean()), 2),
                "min": round(float(df[col].min()), 2),
                "max": round(float(df[col].max()), 2),
                "median": round(float(df[col].median()), 2),
                "std": round(float(df[col].std()), 2),
                "missing": int(df[col].isna().sum())
            }
        else:
            summary[col] = {
                "type": "text",
                "count": int(df[col].count()),
                "unique_values": int(df[col].nunique()),
                "most_common": str(df[col].mode()[0]) if not df[col].mode().empty else None,
                "missing": int(df[col].isna().sum())
            }
    summary["_file_info"] = {
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "columns": list(df.columns)
    }
    output = io.BytesIO()
    output.write(json.dumps(summary, indent=2).encode())
    summary_name = task["file_name"].rsplit(".", 1)[0] + "_summary.json"
    file_ops.upload_file(summary_name, output.getvalue())
    return {"summary_file": summary_name, "status": "success"}
