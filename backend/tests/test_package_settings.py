import pytest
from pydantic import ValidationError
from test_full_settings import settings


def test_document_master_key_must_be_exact_secret_256_bits():
    config = settings(document_encryption_key='a'*64)
    assert hasattr(config, 'document_encryption_key'), 'Document encryption setting missing'
    assert 'a'*64 not in repr(config)
    for key in ['a'*63, 'g'*64, 'x']:
        with pytest.raises(ValidationError):
            settings(document_encryption_key=key)


def test_storage_endpoint_requires_secure_or_local_transport_without_credentials():
    for endpoint in ['http://s3.example', 'https://user:secret@s3.example', 'https://s3.example?secret=x']:
        with pytest.raises(ValidationError):
            settings(s3_endpoint_url=endpoint)
    assert settings(s3_endpoint_url='http://127.0.0.1:53900').s3_endpoint_url
    with pytest.raises(ValidationError):
        settings(environment='production', database_url='postgresql://app:pass@db/tender',
                 public_origin='https://tender.example', s3_endpoint_url='http://127.0.0.1:53900')


def test_extracted_token_limit_cannot_be_configured_above_contract():
    with pytest.raises(ValidationError):
        settings(max_package_tokens=250_001)
