import zipfile
import io                    #fix one 
from minio_client import get_client, get_bucket_name, ensure_bucket
import sorter

def upload_file(file_name: str, data: bytes):
    client = get_client()
    bucket = ensure_bucket(client)
    client.put_object(
        bucket,
        file_name,
        io.BytesIO(data),
        length=len(data)
    )
    return f"{file_name} uploaded successfully"


def download_file(file_name: str) -> bytes:
    client = get_client()
    bucket = get_bucket_name()
    response = client.get_object(bucket, file_name)
    data = response.read()
    response.close()
    return data

def download_as_zip(file_name: str) -> bytes:
    data = download_file(file_name)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(file_name, data)
    return zip_buffer.getvalue()

def download_multiple_as_zip(file_names: list) -> bytes:
    client = get_client()
    bucket = get_bucket_name()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_name in file_names:
            response = client.get_object(bucket, file_name)
            zf.writestr(file_name, response.read())
            response.close()
    return zip_buffer.getvalue()

def sort_and_store(file_name: str, column: str, algorithm: str = "quick") -> str:
    data = download_file(file_name)
    sorted_data = sorter.sort_file(data, file_name, column, algorithm)
    ext = file_name.rsplit(".", 1)[-1]
    base = file_name.rsplit(".", 1)[0]
    sorted_name = f"{base}_sorted.{ext}"
    upload_file(sorted_name, sorted_data)
    return sorted_name