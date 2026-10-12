"""Private S3 bytes adapter with bounded reads and sanitized failures."""
import re

import boto3
from botocore.config import Config

from .crypto import MAX_DOCUMENT_BYTES


class StorageUnavailable(Exception):
    pass


class S3Store:
    def __init__(self, settings):
        if not all([settings.s3_endpoint_url, settings.s3_bucket, settings.s3_access_key, settings.s3_secret_key]):
            raise StorageUnavailable()
        self.bucket = settings.s3_bucket
        self.client = boto3.client('s3', endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=Config(signature_version='s3v4', s3={'addressing_style':'path'},
                          connect_timeout=5, read_timeout=30, retries={'max_attempts':2}))

    def _key(self, key):
        if not isinstance(key,str) or not re.fullmatch(r'org/[0-9a-f-]{36}/project/[0-9a-f-]{36}/attempt/[0-9a-f]{64}/[0-9a-f-]{36}',key):
            raise StorageUnavailable()

    def put(self, key, data):
        self._key(key)
        if len(data) > MAX_DOCUMENT_BYTES + 100_000:
            raise StorageUnavailable()
        try:
            self.client.put_object(Bucket=self.bucket,Key=key,Body=data,ContentType='application/octet-stream')
        except Exception:
            raise StorageUnavailable() from None

    def get(self, key):
        self._key(key)
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket,Key=key)
            body = response['Body']
            data = body.read(MAX_DOCUMENT_BYTES + 100_001)
            if len(data) > MAX_DOCUMENT_BYTES + 100_000:
                raise StorageUnavailable()
            return data
        except Exception:
            raise StorageUnavailable() from None
        finally:
            if body is not None:
                body.close()

    def delete(self, key):
        self._key(key)
        try:
            self.client.delete_object(Bucket=self.bucket,Key=key)
        except Exception:
            raise StorageUnavailable() from None
