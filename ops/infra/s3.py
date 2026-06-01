"""Thin S3 client for sync operations.

Wraps boto3 to provide upload/download/list/delete for the factor library
remote. Configured via the `sync.s3` section in config.yaml.
"""
import os
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


class S3Client:
    def __init__(self, endpoint_url: str, access_key_id: str,
                 secret_access_key: str, bucket: str):
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )

    def upload(self, local: Path, key: str) -> None:
        self._client.upload_file(str(local), self._bucket, key)

    def download(self, key: str, local: Path) -> bool:
        """Download key to local path. Returns False if key doesn't exist."""
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self._bucket, key, str(local))
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def list_prefix(self, prefix: str, suffix: str = "") -> list[str]:
        """List object keys under prefix, optionally filtered by suffix."""
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if not suffix or k.endswith(suffix):
                    keys.append(k)
        return keys

    def list_objects(self, prefix: str) -> list[dict]:
        """List object metadata under prefix.

        Returns list of {key, size, mtime (epoch float), etag} dicts.
        Used by the sync diff engine to compare against local inventory.
        """
        out: list[dict] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append({
                    "key": obj["Key"],
                    "size": int(obj["Size"]),
                    "mtime": obj["LastModified"].timestamp(),
                    "etag": obj.get("ETag", "").strip('"'),
                })
        return out

    def upload_dir(self, local_dir: Path, prefix: str) -> int:
        """Upload all files under local_dir to prefix/. Returns file count."""
        count = 0
        for dirpath, _, filenames in os.walk(local_dir):
            for fname in filenames:
                fpath = Path(dirpath) / fname
                rel = fpath.relative_to(local_dir)
                key = f"{prefix}/{rel}" if prefix else str(rel)
                self._client.upload_file(str(fpath), self._bucket, key)
                count += 1
        return count

    def download_dir(self, prefix: str, local_dir: Path) -> int:
        """Download all objects under prefix/ to local_dir. Returns file count."""
        count = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix):].lstrip("/")
                if not rel:
                    continue
                local_path = local_dir / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                self._client.download_file(self._bucket, key, str(local_path))
                count += 1
        return count

    def list_names(self, prefix: str, suffix: str = "") -> list[str]:
        """List basenames (without prefix and suffix) under prefix."""
        keys = self.list_prefix(prefix, suffix)
        names = []
        for k in keys:
            base = k[len(prefix):].lstrip("/")
            if suffix:
                base = base[:-len(suffix)]
            names.append(base)
        return names
