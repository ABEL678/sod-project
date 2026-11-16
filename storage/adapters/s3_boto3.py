"""Адаптер S3-шлюза на boto3 (MinIO / Cloud.ru).

Настройки — sod_core/settings.py блок S3.
"""

import json
import logging
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from django.conf import settings

from storage.ports.s3_gateway import S3Gateway

logger = logging.getLogger(__name__)


class S3Boto3Gateway(S3Gateway):
    """Реализация S3Gateway через boto3."""

    def __init__(self):
        self._client: Optional[object] = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION or "us-east-1",
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=10,
                    read_timeout=30,
                ),
            )
        return self._client

    def _bucket(self) -> str:
        if not settings.S3_BUCKET_NAME:
            raise RuntimeError("S3_BUCKET_NAME не задан")
        return settings.S3_BUCKET_NAME

    def ensure_bucket(self) -> None:
        client = self._get_client()
        bucket = self._bucket()
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket"):
                client.create_bucket(Bucket=bucket)
                logger.info("S3 bucket %s created", bucket)
            else:
                raise

    def put_json_object(self, key: str, data: bytes) -> None:
        client = self._get_client()
        client.put_object(
            Bucket=self._bucket(),
            Key=key,
            Body=data,
            ContentType="application/json",
        )

    def get_json_object(self, key: str) -> Optional[dict]:
        client = self._get_client()
        try:
            response = client.get_object(Bucket=self._bucket(), Key=key)
            body = response["Body"].read()
            return json.loads(body)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise

    def list_keys(self, prefix: str) -> list[str]:
        client = self._get_client()
        keys = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket(), Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def move_object(self, source: str, dest: str) -> None:
        client = self._get_client()
        bucket = self._bucket()
        client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": source},
            Key=dest,
        )
        client.delete_object(Bucket=bucket, Key=source)

    def delete_object(self, key: str) -> None:
        client = self._get_client()
        client.delete_object(Bucket=self._bucket(), Key=key)

    def object_exists(self, key: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket(), Key=key)
            return True
        except ClientError:
            return False