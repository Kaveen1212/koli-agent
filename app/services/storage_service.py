import os
import uuid
import boto3
from botocore.config import Config
from app.config import (
    STORAGE_BUCKET,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_REGION,
    AWS_ENDPOINT_URL,
    AWS_CLOUDFRONT_URL,
    PUBLIC_BASE_URL,
)

_client = None


def storage_enabled() -> bool:
    """True when S3/R2 credentials are configured; otherwise use local disk."""
    return bool(AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and STORAGE_BUCKET)


def _get_client():
    """Build the boto3 client lazily — only when storage is actually used."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            endpoint_url=AWS_ENDPOINT_URL or None,
            config=Config(signature_version="s3v4"),
        )
    return _client


def upload_image(file_bytes: bytes, filename: str) -> str:
    """Upload image bytes to S3/R2. Returns the object key."""
    object_key = f"uploads/{filename}"
    _get_client().put_object(
        Bucket=STORAGE_BUCKET,
        Key=object_key,
        Body=file_bytes,
        ContentType="image/jpeg",
    )
    return object_key


def get_image_url(object_key: str) -> str:
    """Return the public URL for a stored object."""
    if AWS_CLOUDFRONT_URL:
        return f"{AWS_CLOUDFRONT_URL.rstrip('/')}/{object_key}"
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": STORAGE_BUCKET, "Key": object_key},
        ExpiresIn=3600,
    )


def download_image(object_key: str) -> bytes:
    """Download image bytes from S3/R2."""
    response = _get_client().get_object(Bucket=STORAGE_BUCKET, Key=object_key)
    return response["Body"].read()


def save_image(image_bytes: bytes, prefix: str = "generations",
               filename: str | None = None) -> tuple[str, str]:
    """Save image bytes to S3/R2 if configured, else to local disk.
    Returns (object_key, public_url). Used by every image-producing path.
    """
    filename = filename or f"{uuid.uuid4()}.jpg"
    object_key = f"{prefix}/{filename}"

    if storage_enabled():
        _get_client().put_object(
            Bucket=STORAGE_BUCKET,
            Key=object_key,
            Body=image_bytes,
            ContentType="image/jpeg",
        )
        return object_key, get_image_url(object_key)

    # Local disk fallback for development
    local_dir = os.path.join("static", prefix)
    os.makedirs(local_dir, exist_ok=True)
    with open(os.path.join(local_dir, filename), "wb") as f:
        f.write(image_bytes)
    return object_key, f"{PUBLIC_BASE_URL}/static/{object_key}"
