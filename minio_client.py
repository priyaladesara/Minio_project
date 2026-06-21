import os
from minio import Minio

def get_client():
    client = Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=False
    )
    return client

def get_bucket_name():
    return os.getenv("BUCKET_NAME", "my-bucket")

BUCKET_NAME = get_bucket_name()   # keep for backward compat

def ensure_bucket(client):
    bucket = get_bucket_name()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket