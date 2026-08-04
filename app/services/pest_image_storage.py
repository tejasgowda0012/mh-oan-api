"""MinIO-backed storage for temporary pest-detection images."""

from typing import Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


class PestImageStorageError(RuntimeError):
    """Raised when the configured MinIO object store cannot be used."""


_BUCKET_NOT_FOUND_CODES: Final = {"404", "NoSuchBucket", "NotFound"}
_OBJECT_NOT_FOUND_CODES: Final = {"404", "NoSuchKey", "NotFound"}


class PestImageStorage:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not settings.minio_endpoint_url:
            raise PestImageStorageError("MINIO_ENDPOINT_URL is not configured")
        if not settings.minio_access_key or not settings.minio_secret_key:
            raise PestImageStorageError("MinIO access credentials are not configured")

        self._client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name=settings.minio_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return self._client

    def _ensure_bucket(self) -> None:
        client = self._get_client()
        try:
            client.head_bucket(Bucket=settings.minio_pest_upload_bucket)
            return
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in _BUCKET_NOT_FOUND_CODES:
                raise PestImageStorageError(
                    "Unable to access the MinIO upload bucket"
                ) from exc

        try:
            client.create_bucket(Bucket=settings.minio_pest_upload_bucket)
        except (ClientError, BotoCoreError) as exc:
            raise PestImageStorageError(
                "Unable to create the MinIO upload bucket"
            ) from exc

    def put_image(self, object_key: str, content: bytes, content_type: str) -> None:
        try:
            self._ensure_bucket()
            self._get_client().put_object(
                Bucket=settings.minio_pest_upload_bucket,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )
        except PestImageStorageError:
            raise
        except (ClientError, BotoCoreError) as exc:
            raise PestImageStorageError(
                "Unable to store pest upload in MinIO"
            ) from exc

    def get_image(self, object_key: str) -> bytes:
        try:
            response = self._get_client().get_object(
                Bucket=settings.minio_pest_upload_bucket,
                Key=object_key,
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _OBJECT_NOT_FOUND_CODES:
                raise FileNotFoundError("Pest upload object was not found") from exc
            raise PestImageStorageError(
                "Unable to retrieve pest upload from MinIO"
            ) from exc
        except BotoCoreError as exc:
            raise PestImageStorageError(
                "Unable to retrieve pest upload from MinIO"
            ) from exc

        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()


pest_image_storage = PestImageStorage()
