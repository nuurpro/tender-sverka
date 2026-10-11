import pytest
from pydantic import ValidationError


def settings(**overrides):
    from app.config import Settings
    values = {
        "environment": "test", "database_url": "sqlite+pysqlite:///:memory:",
        "session_secret": "a" * 48,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_sqlite_is_only_allowed_for_explicit_test_environment():
    assert settings().environment == "test"
    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings(environment="development")
    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings(environment="production")


def test_production_rejects_weak_secrets_and_local_origins():
    base = {"environment": "production", "database_url": "postgresql+psycopg://app:pass@db/tenders"}
    with pytest.raises(ValidationError, match="session secret"):
        settings(**base, session_secret="change-me", public_origin="https://tenders.example")
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(**base, public_origin="http://localhost:3000")


def test_production_requires_explicit_non_owner_database_role():
    with pytest.raises(ValidationError, match="restricted application role"):
        settings(environment="production", database_url="postgresql+psycopg://postgres:pass@db/tenders",
                 public_origin="https://tenders.example")


def test_budgets_are_validated_and_default_limits_match_agreed_plan():
    config = settings()
    assert (config.max_package_files, config.max_package_bytes, config.max_package_pages) == (20, 50_000_000, 1000)
    assert config.max_package_tokens == 250_000
    assert (config.max_buyer_participants, config.max_supplier_participants) == (5, 1)
    with pytest.raises(ValidationError):
        settings(max_package_tokens=0)
    with pytest.raises(ValidationError):
        settings(max_package_bytes=-1)


def test_configuration_representation_does_not_expose_secrets():
    config = settings(database_url="postgresql+psycopg://app:database-password@db/tenders",
                      gateway_api_key="gateway-key-sensitive", broker_url="amqp://app:broker-password@mq//")
    rendered = repr(config)
    assert "database-password" not in rendered
    assert "gateway-key-sensitive" not in rendered
    assert "broker-password" not in rendered
    assert "a" * 48 not in rendered


def test_configuration_validation_messages_do_not_echo_secret_inputs():
    with pytest.raises(ValidationError) as error:
        settings(environment="production", database_url="postgresql://postgres:secret-database-password@db/tenders",
                 gateway_api_key="secret-gateway-token", session_secret="secret-too-short",
                 public_origin="https://tenders.example")
    rendered = str(error.value)
    assert "secret-database-password" not in rendered
    assert "secret-gateway-token" not in rendered
    assert "secret-too-short" not in rendered


def test_gateway_is_default_but_external_ai_requires_organization_policy():
    config = settings()
    assert config.gateway_api_base == "https://agentrouter.org/v1"
    assert config.gateway_api_style == "chat"
    assert config.external_ai_default_allowed is False


def test_gateway_url_rejects_credentials_and_insecure_external_transport():
    with pytest.raises(ValidationError, match="gateway"):
        settings(gateway_api_base="https://secret:password@agentrouter.org/v1")
    with pytest.raises(ValidationError, match="gateway"):
        settings(gateway_api_base="http://agentrouter.org/v1")
