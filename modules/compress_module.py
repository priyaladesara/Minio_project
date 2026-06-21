import file_ops
import zipfile
import io

MODULE_NAME = "compress"

def process(task: dict) -> dict:
    data = file_ops.download_file(task["file_name"])
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(task["file_name"], data)
    zip_name = task["file_name"].rsplit(".", 1)[0] + ".zip"
    file_ops.upload_file(zip_name, zip_buffer.getvalue())
    return {"compressed_file": zip_name, "status": "success"}