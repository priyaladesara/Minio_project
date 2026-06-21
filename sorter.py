import pandas as pd
import io

def bubble_sort(arr: list) -> list:
    arr = arr.copy()
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr

def quick_sort(arr: list) -> list:
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quick_sort(left) + middle + quick_sort(right)

ALGORITHMS = {
    "bubble": bubble_sort,
    "quick": quick_sort
}

def sort_file(data: bytes, file_name: str, column: str, algorithm: str = "quick") -> bytes:
    """Read a file, sort by column, return sorted bytes in same format."""
    ext = file_name.rsplit(".", 1)[-1].lower()

    # Read file into dataframe
    if ext == "csv":
        df = pd.read_csv(io.BytesIO(data))
    elif ext in ["xlsx", "xls"]:
        df = pd.read_excel(io.BytesIO(data))
    elif ext == "json":
        df = pd.read_json(io.BytesIO(data))
    elif ext == "parquet":
        df = pd.read_parquet(io.BytesIO(data))
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    # Sort the column using chosen algorithm
    col_data = df[column].tolist()
    algo = ALGORITHMS.get(algorithm)
    if not algo:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    sorted_col = algo(col_data)
    df = df.iloc[[col_data.index(x) for x in sorted_col]].reset_index(drop=True)

    # Write back to same format
    output = io.BytesIO()
    if ext == "csv":
        df.to_csv(output, index=False)
    elif ext in ["xlsx", "xls"]:
        df.to_excel(output, index=False)
    elif ext == "json":
        df.to_json(output, orient="records")
    elif ext == "parquet":
        df.to_parquet(output, index=False)

    return output.getvalue()