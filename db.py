import os
import psycopg2
from datetime import datetime

def get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "hostname"),
        port=5432,
        dbname=os.getenv("POSTGRES_DB", "yourdb"),
        user=os.getenv("POSTGRES_USER", "admin"),
        password=os.getenv("POSTGRES_PASSWORD", "yourpassword")
    )

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_stats (
            id SERIAL PRIMARY KEY,
            task_id VARCHAR(100),
            file_name VARCHAR(255),
            module VARCHAR(50),
            queue_name VARCHAR(100),
            status VARCHAR(20),
            error TEXT,
            result_file VARCHAR(255),
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            duration_secs FLOAT,
            worker_pod VARCHAR(255)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def save_stat(task_id, file_name, module, queue_name, status, 
              error, result_file, started_at, finished_at, worker_pod):
    conn = get_conn()
    cur = conn.cursor()
    duration = (finished_at - started_at).total_seconds()
    cur.execute("""
        INSERT INTO task_stats 
        (task_id, file_name, module, queue_name, status, error, 
         result_file, started_at, finished_at, duration_secs, worker_pod)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (task_id, file_name, module, queue_name, status, error,
          result_file, started_at, finished_at, duration, worker_pod))
    conn.commit()
    cur.close()
    conn.close()