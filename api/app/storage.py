"""Работа с S3-хранилищем (SeaweedFS): видеофайлы и превью."""
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import S3_ACCESS_KEY, S3_BUCKET, S3_SECRET_KEY, STORAGE_URL

s3 = boto3.client(
    "s3",
    endpoint_url=STORAGE_URL,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
)


def ensure_bucket():
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=S3_BUCKET)


def upload(fileobj, key: str, content_type: str):
    s3.upload_fileobj(fileobj, S3_BUCKET, key, ExtraArgs={"ContentType": content_type})


def get(key: str, byte_range: str | None = None):
    kwargs = {"Bucket": S3_BUCKET, "Key": key}
    if byte_range:
        kwargs["Range"] = byte_range
    return s3.get_object(**kwargs)


def delete(*keys: str):
    for key in keys:
        if key:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
