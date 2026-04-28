"""
One-time upload script: pushes ENR xlsx files and static data files to Vercel Blob.

Usage:
    python upload_data.py --data-dir ./data

Requires BLOB_READ_WRITE_TOKEN in environment or .env file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


def blob_token() -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not token:
        sys.exit("ERROR: BLOB_READ_WRITE_TOKEN not set")
    return token


def upload_file(local_path: Path, blob_path: str) -> str:
    """Upload a file to Vercel Blob and return its public URL."""
    suffix = local_path.suffix.lower()
    content_types = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".json": "application/json",
    }
    content_type = content_types.get(suffix, "application/octet-stream")

    print(f"  Uploading {local_path.name} → {blob_path} ...", end=" ", flush=True)
    with open(local_path, "rb") as f:
        resp = requests.put(
            f"https://blob.vercel-storage.com/{blob_path}",
            headers={
                "Authorization": f"Bearer {blob_token()}",
                "x-content-type": content_type,
            },
            data=f,
            timeout=120,
        )
    resp.raise_for_status()
    url = resp.json()["url"]
    print("OK")
    return url


def main():
    parser = argparse.ArgumentParser(description="Upload ENR data files to Vercel Blob")
    parser.add_argument("--data-dir", default="./data", help="Path to data/ directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        sys.exit(f"ERROR: data directory not found: {data_dir}")

    # Upload ENR xlsx files
    enr_dir = data_dir / "enr"
    if enr_dir.exists():
        xlsx_files = sorted(enr_dir.glob("*.xlsx"))
        if not xlsx_files:
            print(f"WARNING: No .xlsx files found in {enr_dir}")
        else:
            print(f"\nUploading {len(xlsx_files)} ENR files...")
            for f in xlsx_files:
                upload_file(f, f"enr-data/{f.name}")
    else:
        print(f"WARNING: enr/ directory not found in {data_dir}")

    # Upload static files
    print("\nUploading static data files...")
    for filename in ["cci.xlsx", "fmi_forecast.json"]:
        local = data_dir / filename
        if local.exists():
            upload_file(local, f"static-data/{filename}")
        else:
            print(f"  SKIP: {filename} not found")

    print("\nAll uploads complete.")
    print("You can verify at: https://vercel.com/dashboard → Storage → your Blob store")


if __name__ == "__main__":
    main()
