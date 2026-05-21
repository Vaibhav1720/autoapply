"""Blob Storage client for the AutoApply API."""

import os
from datetime import datetime, timedelta, timezone
from azure.storage.blob import (
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
    BlobSasPermissions,
)

_blob_service = None


def get_blob_service() -> BlobServiceClient:
    """Get or create the singleton BlobServiceClient."""
    global _blob_service
    if _blob_service is None:
        conn_str = os.environ["BLOB_CONNECTION_STRING"]
        _blob_service = BlobServiceClient.from_connection_string(conn_str)
    return _blob_service


def upload_blob(container: str, blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Upload data to a blob and return its URL."""
    service = get_blob_service()
    blob_client = service.get_blob_client(container=container, blob=blob_name)
    blob_client.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type=content_type))
    return blob_client.url


def get_blob_url(container: str, blob_name: str) -> str:
    """Get the URL for a blob."""
    service = get_blob_service()
    blob_client = service.get_blob_client(container=container, blob=blob_name)
    return blob_client.url


def generate_sas_url(container: str, blob_name: str, expiry_hours: int = 1) -> str:
    """Generate a SAS URL for read access to a blob."""
    service = get_blob_service()
    account_name = service.account_name
    account_key = service.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )
    blob_url = get_blob_url(container, blob_name)
    return f"{blob_url}?{sas_token}"


def download_blob(container: str, blob_name: str) -> bytes:
    """Download blob content as bytes."""
    service = get_blob_service()
    blob_client = service.get_blob_client(container=container, blob=blob_name)
    return blob_client.download_blob().readall()


def delete_blob(container: str, blob_name: str) -> None:
    """Delete a blob."""
    service = get_blob_service()
    blob_client = service.get_blob_client(container=container, blob=blob_name)
    blob_client.delete_blob()
