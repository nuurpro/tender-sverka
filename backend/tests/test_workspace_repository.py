"""SQLite is an isolated test adapter; production uses PostgreSQL and RLS."""
from uuid import uuid4
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture
def workspace():
    try:
        from app.workspace.database import create_engine
        from app.workspace.models import Base, Organization, User
        from app.workspace.repository import Repository
    except ImportError as exc:
        pytest.fail(f"Versioned workspace persistence is missing: {exc}")
    engine = create_engine("sqlite+pysqlite:///:memory:", allow_sqlite_for_tests=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        a, b, actor = str(uuid4()), str(uuid4()), str(uuid4())
        session.add_all([Organization(id=a, name="A"), Organization(id=b, name="B"),
                         User(id=actor, email="member@example.com")])
        session.commit()
        yield session, Repository(session, a, actor), Repository(session, b, actor)
    engine.dispose()


def requirement(item="Original value", **overrides):
    return {"display_id": "T-1", "scope": "product", "item": item,
            "category": "technical", "importance": "mandatory", "parameter": "Quantity",
            "condition": "At least one", **overrides}


def approved(repo, project):
    draft = repo.create_checklist_draft(project.id, [requirement()])
    return repo.approve_checklist(draft.id, draft.revision)


def test_approved_checklist_is_immutable_and_requires_nonempty_draft(workspace):
    from app.workspace.repository import Conflict, ValidationError
    _, repo, _ = workspace
    project = repo.create_project("supplier", "Tender")
    empty = repo.create_checklist_draft(project.id)
    with pytest.raises(ValidationError):
        repo.approve_checklist(empty.id, empty.revision)
    version = approved(repo, project)
    with pytest.raises(Conflict):
        repo.save_checklist_draft(version.id, [requirement("Changed")], version.revision, "Edit")
    assert repo.list_requirements(version.id)[0].item == "Original value"
    assert version.approved_by == repo.actor_id
    assert version.approved_at is not None


def test_fork_retains_history_and_logical_identity_with_optimistic_revision(workspace):
    from app.workspace.repository import Conflict, ValidationError
    _, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    first = approved(repo, project)
    original = repo.list_requirements(first.id)[0]
    draft = repo.fork_checklist(first.id)
    payload = requirement("Updated", logical_id=original.logical_id)
    with pytest.raises(ValidationError):
        repo.save_checklist_draft(draft.id, [payload], draft.revision)
    initial_revision = draft.revision
    repo.save_checklist_draft(draft.id, [payload], initial_revision, "Clarified tender")
    with pytest.raises(Conflict):
        repo.save_checklist_draft(draft.id, [payload], initial_revision, "Stale edit")
    second = repo.approve_checklist(draft.id, draft.revision)
    assert repo.list_requirements(first.id)[0].item == "Original value"
    assert repo.list_requirements(second.id)[0].item == "Updated"
    assert repo.list_requirements(second.id)[0].logical_id == original.logical_id
    assert second.parent_version_id == first.id
    assert [v.version for v in repo.list_checklist_history(project.id)] == [1, 2]


def test_fork_records_requirement_lineage_without_rewriting_source_origin(workspace):
    _, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    draft = repo.create_checklist_draft(project.id, [requirement(origin={"source": "document-page-3"})])
    source = repo.approve_checklist(draft.id, draft.revision)
    original = repo.list_requirements(source.id)[0]
    fork = repo.fork_checklist(source.id)
    descendant = repo.list_requirements(fork.id)[0]
    assert descendant.origin == {"source": "document-page-3", "parent_checklist_id": source.id,
                                 "parent_requirement_id": original.id}
    assert repo.list_requirements(source.id)[0].origin == {"source": "document-page-3"}


def test_tenant_cannot_read_or_mutate_foreign_projects_or_versions(workspace):
    from app.workspace.repository import NotFound
    _, a, b = workspace
    foreign = b.create_project("buyer", "Private")
    checklist = b.create_checklist_draft(foreign.id, [requirement()])
    for operation in [lambda: a.get_project(foreign.id),
                      lambda: a.create_checklist_draft(foreign.id),
                      lambda: a.get_checklist(checklist.id),
                      lambda: a.save_checklist_draft(checklist.id, [], 1, "Attempt"),
                      lambda: a.approve_checklist(checklist.id, 1),
                      lambda: a.create_participant(foreign.id, "Intruder", 1),
                      lambda: a.create_job("extract", foreign.id, {}, "foreign")]:
        with pytest.raises(NotFound):
            operation()
    assert a.list_projects() == []
    assert a.list_audit() == []


def test_participant_limits_slots_and_project_uniqueness(workspace):
    from app.workspace.repository import Conflict, ValidationError
    _, repo, _ = workspace
    supplier = repo.create_project("supplier", "Supplier")
    buyer = repo.create_project("buyer", "Buyer")
    p = repo.create_participant(supplier.id, "Us", 1)
    assert repo.get_participant(p.id).name == "Us"
    with pytest.raises(Conflict):
        repo.create_participant(supplier.id, "Another", 1)
    with pytest.raises(ValidationError):
        repo.create_participant(supplier.id, "Another", 2)
    for slot in range(1, 6):
        repo.create_participant(buyer.id, f"Participant {slot}", slot)
    for slot in [0, 6]:
        with pytest.raises(ValidationError):
            repo.create_participant(buyer.id, "Invalid", slot)
    with pytest.raises(Conflict):
        repo.create_participant(buyer.id, "Duplicate slot", 1)
    with pytest.raises(ValidationError):
        repo.create_project("invalid", "Bad")
    assert len(repo.list_participants(buyer.id)) == 5


def test_audit_contains_actor_reason_snapshots_and_rolls_back_with_mutation(workspace):
    from app.workspace.models import AuditEvent, Project
    session, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    version = approved(repo, project)
    draft = repo.fork_checklist(version.id)
    row = repo.list_requirements(draft.id)[0]
    repo.save_checklist_draft(draft.id, [requirement("Updated", logical_id=row.logical_id)],
                              draft.revision, "Corrected source")
    audit = repo.list_audit(project.id)[-1]
    assert audit.actor_id == repo.actor_id
    assert audit.organization_id == repo.organization_id
    assert audit.reason == "Corrected source"
    assert audit.before["requirements"][0]["item"] == "Original value"
    assert audit.after["requirements"][0]["item"] == "Updated"
    assert audit.created_at is not None
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Project)) == 0
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_job_idempotency_canonical_input_and_outbox_transaction(workspace):
    from app.workspace.models import Job, OutboxEvent
    from app.workspace.repository import Conflict
    session, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    first = repo.create_job("extract", project.id, {"b": 2, "a": 1}, "request-1")
    again = repo.create_job("extract", project.id, {"a": 1, "b": 2}, "request-1")
    assert first.id == again.id
    assert len(first.input_hash) == 64
    with pytest.raises(Conflict):
        repo.create_job("extract", project.id, {"a": 3}, "request-1")
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    assert repo.get_job(first.id).status == "queued"
    other = repo.create_project("buyer", "Other")
    assert repo.create_job("extract", other.id, {"a": 3}, "request-1").id != first.id
    session.rollback()
    assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_production_engine_rejects_implicit_sqlite():
    try:
        from app.workspace.database import create_engine
    except ImportError as exc:
        pytest.fail(f"Production database configuration missing: {exc}")
    with pytest.raises(ValueError, match="PostgreSQL"):
        create_engine("sqlite:///:memory:")


def test_composite_foreign_keys_reject_cross_tenant_and_cross_project_links(workspace):
    from sqlalchemy.exc import IntegrityError
    from app.workspace.models import ChecklistVersion, Participant, PackageRevision
    session, a, b = workspace
    pa = a.create_project("buyer", "A")
    pb = b.create_project("buyer", "B")
    participant = a.create_participant(pa.id, "A bidder", 1)
    session.commit()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(ChecklistVersion(organization_id=a.organization_id, project_id=pb.id, version=1))
        session.flush()
    second = a.create_project("buyer", "A2")
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(PackageRevision(organization_id=a.organization_id, project_id=second.id,
                                    kind="bid", participant_id=participant.id, version=1))
        session.flush()


def test_transaction_helper_commits_and_rolls_back_entire_repository_operation():
    from app.workspace.database import create_engine, tenant_transaction
    from app.workspace.models import Base, Organization, User, Project, AuditEvent
    from app.workspace.repository import Repository
    engine = create_engine("sqlite:///:memory:", allow_sqlite_for_tests=True)
    Base.metadata.create_all(engine)
    org, actor = str(uuid4()), str(uuid4())
    with Session(engine) as session, session.begin():
        session.add_all([Organization(id=org, name="Tenant"), User(id=actor, email="tx@example.com")])
    with tenant_transaction(engine, org) as session:
        Repository(session, org, actor).create_project("buyer", "Committed")
    with pytest.raises(RuntimeError), tenant_transaction(engine, org) as session:
        Repository(session, org, actor).create_project("buyer", "Rolled back")
        raise RuntimeError("Abort")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    engine.dispose()


def test_tender_package_versions_are_unique_even_with_null_participant(workspace):
    from sqlalchemy.exc import IntegrityError
    from app.workspace.models import PackageRevision
    session, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    session.add(PackageRevision(organization_id=repo.organization_id, project_id=project.id, kind="tender", version=1))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(PackageRevision(organization_id=repo.organization_id, project_id=project.id, kind="tender", version=1))
        session.flush()


def test_sqlite_test_migration_matches_model_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from app.workspace.database import create_engine
    from app.workspace.models import Base
    database_url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ALEMBIC_ALLOW_SQLITE_TESTS", "1")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(database_url, allow_sqlite_for_tests=True)
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_participant_requirements_allow_null_item_and_zero_padded_display_id(workspace):
    _, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    draft = repo.create_checklist_draft(project.id, [requirement(None, scope="participant", display_id="U-007"),
                                                   requirement(display_id="T-014")])
    repo.approve_checklist(draft.id, draft.revision)
    rows = repo.list_requirements(draft.id)
    assert {row.display_id for row in rows} == {"T-014", "U-007"}
    assert next(row for row in rows if row.scope == "participant").item is None


@pytest.mark.parametrize("field,value", [("scope", "tender"), ("scope", "unknown"),
                                          ("importance", "optional"), ("importance", "unknown")])
def test_requirement_domain_values_are_validated_before_mutation(workspace, field, value):
    from app.workspace.repository import ValidationError
    _, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    with pytest.raises(ValidationError):
        repo.create_checklist_draft(project.id, [requirement(**{field: value})])
    assert repo.list_checklist_history(project.id) == []


@pytest.mark.parametrize("language", ["ru", "uz", "en"])
def test_report_languages_accept_only_product_supported_values(workspace, language):
    from app.workspace.repository import ValidationError
    _, repo, _ = workspace
    assert repo.create_project("buyer", "Tender", report_language=language).report_language == language
    with pytest.raises(ValidationError):
        repo.create_project("buyer", "Tender", report_language="de")


@pytest.mark.parametrize("field,value", [("parameter", None), ("parameter", ""), ("condition", " ")])
def test_approval_rejects_incomplete_draft_and_can_be_completed(workspace, field, value):
    from app.workspace.repository import ValidationError
    _, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    draft = repo.create_checklist_draft(project.id, [requirement(**{field: value})])
    with pytest.raises(ValidationError, match="parameter|condition"):
        repo.approve_checklist(draft.id, draft.revision)
    assert draft.status == "draft"
    logical_id = repo.list_requirements(draft.id)[0].logical_id
    repo.save_checklist_draft(draft.id, [requirement(logical_id=logical_id)], draft.revision, "Completed source details")
    assert repo.approve_checklist(draft.id, draft.revision).status == "approved"


def test_database_checks_reject_requirement_and_report_language_domain_bypass(workspace):
    from sqlalchemy.exc import IntegrityError
    from app.workspace.models import Project, Requirement
    session, repo, _ = workspace
    project = repo.create_project("buyer", "Tender")
    draft = repo.create_checklist_draft(project.id)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Requirement(organization_id=repo.organization_id, checklist_id=draft.id,
                                **requirement(scope="unknown")))
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Project(organization_id=repo.organization_id, mode="buyer", lot_name="Invalid", report_language="de"))
        session.flush()


@pytest.fixture
def postgres_workspace():
    """Explicit disposable PostgreSQL URLs; no fallback or production schema creation."""
    from app.workspace.database import create_engine, tenant_transaction
    from app.workspace.models import Organization, User
    from app.workspace.repository import Repository
    owner_url = os.environ.get("WORKSPACE_TEST_DATABASE_URL")
    runtime_url = os.environ.get("WORKSPACE_TEST_RUNTIME_DATABASE_URL")
    if not owner_url or not runtime_url:
        pytest.skip("Dedicated PostgreSQL owner/runtime test URLs are not configured")
    owner, runtime = create_engine(owner_url), create_engine(runtime_url)
    a, b, actor = str(uuid4()), str(uuid4()), str(uuid4())
    with Session(owner) as session, session.begin():
        session.add_all([Organization(id=a, name="PG Tenant A"), Organization(id=b, name="PG Tenant B"),
                         User(id=actor, email=f"{actor}@example.com")])
    with tenant_transaction(runtime, b) as session:
        foreign = Repository(session, b, actor).create_project("buyer", "Private PG project")
        foreign_id = foreign.id
    yield runtime, a, b, actor, foreign_id
    runtime.dispose()
    owner.dispose()


def test_postgresql_rls_filters_reads_and_blocks_foreign_writes(postgres_workspace):
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    from app.workspace.database import tenant_transaction
    runtime, a, b, actor, foreign_id = postgres_workspace
    with runtime.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 0
        assert connection.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")) is False
    with tenant_transaction(runtime, a) as session:
        assert session.scalar(text("SELECT count(*) FROM projects WHERE id = :id"), {"id": foreign_id}) == 0
        assert session.scalar(text("SELECT count(*) FROM pg_class WHERE relname = 'projects' AND relrowsecurity AND relforcerowsecurity")) == 1
    with pytest.raises(ProgrammingError) as rejection, tenant_transaction(runtime, a) as session:
        session.execute(text("""INSERT INTO projects
            (id, organization_id, mode, lot_name, timezone, report_language, revision, created_at)
            VALUES (:id, :organization, 'buyer', 'Cross tenant', 'Asia/Tashkent', 'ru', 1, now())"""),
            {"id": str(uuid4()), "organization": b})
    assert rejection.value.orig.sqlstate == "42501"


def test_postgresql_blocks_direct_edits_of_approved_requirement_and_manifest(postgres_workspace):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.workspace.database import tenant_transaction
    from app.workspace.models import PackageRevision
    from app.workspace.repository import Repository
    runtime, org, _, actor, _ = postgres_workspace
    with tenant_transaction(runtime, org) as session:
        repo = Repository(session, org, actor)
        project = repo.create_project("buyer", "Frozen source")
        checklist = approved(repo, project)
        requirement_id = repo.list_requirements(checklist.id)[0].id
        checklist_id = checklist.id
        package = PackageRevision(organization_id=org, project_id=project.id, kind="tender", version=1,
                                  manifest={"files": [{"hash": "original"}]})
        session.add(package)
        session.flush()
        package_id = package.id
    for statement, row_id in [
        ("UPDATE requirements SET item = 'Tampered' WHERE id = :id", requirement_id),
        ("DELETE FROM requirements WHERE id = :id", requirement_id),
        ("UPDATE checklist_versions SET status = 'draft' WHERE id = :id", checklist_id),
        ("UPDATE package_revisions SET manifest = '{}'::jsonb WHERE id = :id", package_id),
    ]:
        with pytest.raises(IntegrityError), tenant_transaction(runtime, org) as session:
            session.execute(text(statement), {"id": row_id})
    with tenant_transaction(runtime, org) as session:
        assert Repository(session, org, actor).list_requirements(checklist_id)[0].item == "Original value"


def test_postgresql_concurrent_idempotency_emits_one_job_and_outbox(postgres_workspace):
    from concurrent.futures import ThreadPoolExecutor
    from app.workspace.database import tenant_transaction
    from app.workspace.models import Job, OutboxEvent
    from app.workspace.repository import Repository
    runtime, org, _, actor, _ = postgres_workspace
    with tenant_transaction(runtime, org) as session:
        project_id = Repository(session, org, actor).create_project("buyer", "Concurrency").id

    def submit(_):
        with tenant_transaction(runtime, org) as session:
            return Repository(session, org, actor).create_job("extract", project_id, {"a": 1}, "shared-key").id

    with ThreadPoolExecutor(max_workers=4) as pool:
        job_ids = list(pool.map(submit, range(4)))
    assert len(set(job_ids)) == 1
    with tenant_transaction(runtime, org) as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.project_id == project_id)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.job_id == job_ids[0])) == 1


@pytest.mark.parametrize("field,expression", [
    ("package_ids", "CAST(:tampered_payload AS jsonb)"),
    ("scoring_policy", "CAST(:tampered_payload AS jsonb)"),
    ("provider_config_versions", "CAST(:tampered_payload AS jsonb)"),
    ("checklist_id", ":alternate_checklist"),
    ("deadline", "now()"),
    ("organization_id", ":alternate_org"),
    ("project_id", ":alternate_project"),
    ("id", ":alternate_id"),
    ("created_at", "created_at + interval '1 second'"),
])
def test_postgresql_comparison_snapshot_is_frozen_but_status_can_advance(postgres_workspace, field, expression):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.workspace.database import tenant_transaction
    from app.workspace.models import ComparisonRun
    from app.workspace.repository import Repository
    runtime, org, other_org, actor, _ = postgres_workspace
    with tenant_transaction(runtime, org) as session:
        repo = Repository(session, org, actor)
        project = repo.create_project("buyer", "Frozen comparison")
        checklist = approved(repo, project)
        alternate = approved(repo, project)
        other_project = repo.create_project("buyer", "Alternative project")
        run = ComparisonRun(organization_id=org, project_id=project.id, checklist_id=checklist.id,
                            package_ids=["original"], scoring_policy={"version": 1},
                            provider_config_versions={"model": 1})
        session.add(run)
        session.flush()
        values = {"id": run.id, "alternate_checklist": alternate.id, "alternate_org": other_org,
                  "alternate_project": other_project.id, "alternate_id": str(uuid4()),
                  "tampered_payload": '["tampered"]' if field == "package_ids" else '{"version":2}'}
        run.status = "running"
    with pytest.raises(IntegrityError) as rejection, tenant_transaction(runtime, org) as session:
        session.execute(text(f"UPDATE comparison_runs SET {field} = {expression} WHERE id = :id"), values)
    assert rejection.value.orig.sqlstate == "23514"
    with tenant_transaction(runtime, org) as session:
        assert session.scalar(select(ComparisonRun.status).where(ComparisonRun.id == values["id"])) == "running"


def test_runtime_verification_checks_sqlite_migration_head_not_only_table_presence(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from app.workspace.database import create_engine, verify_runtime_database
    url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALEMBIC_ALLOW_SQLITE_TESTS", "1")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url, allow_sqlite_for_tests=True)
    verify_runtime_database(engine, allow_sqlite_for_tests=True)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'stale_revision'"))
    with pytest.raises(RuntimeError, match="migration"):
        verify_runtime_database(engine, allow_sqlite_for_tests=True)
    engine.dispose()


@pytest.fixture
def postgres_runtime_probe(monkeypatch):
    """Fresh database/role so destructive configuration probes cannot affect shared fixtures."""
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy.engine import make_url
    from app.workspace.database import create_engine
    owner_url = os.environ.get("WORKSPACE_TEST_DATABASE_URL")
    if not owner_url:
        pytest.skip("Dedicated PostgreSQL migration test URL not configured")
    suffix = uuid4().hex
    database_name, probe_role, auxiliary_owner = f"probe_{suffix}", f"runtime_{suffix}", f"owner_{suffix}"
    probe_password = uuid4().hex
    administration = create_engine(owner_url)
    with administration.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        raw = connection.connection.driver_connection
        from psycopg import sql
        raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(probe_role), sql.Literal(probe_password)))
        raw.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(auxiliary_owner)))
        raw.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    isolated_owner_url = make_url(owner_url).set(database=database_name)
    isolated_runtime_url = isolated_owner_url.set(username=probe_role, password=probe_password)
    isolated_owner = create_engine(isolated_owner_url.render_as_string(hide_password=False))
    runtime = create_engine(isolated_runtime_url.render_as_string(hide_password=False))
    try:
        monkeypatch.setenv("DATABASE_URL", isolated_owner_url.render_as_string(hide_password=False))
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        with isolated_owner.begin() as connection:
            raw = connection.connection.driver_connection
            raw.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(probe_role)))
            raw.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(probe_role)))
            raw.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(auxiliary_owner)))
        yield isolated_owner, runtime, probe_role, auxiliary_owner
    finally:
        isolated_owner.dispose()
        runtime.dispose()
        with administration.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            raw = connection.connection.driver_connection
            raw.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
            raw.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(probe_role)))
            raw.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(auxiliary_owner)))
        administration.dispose()


@pytest.mark.parametrize("misconfiguration", ["no_force", "no_rls", "owner_membership", "stale_revision"])
def test_runtime_verification_rejects_postgresql_security_and_revision_drift(postgres_runtime_probe, misconfiguration):
    from sqlalchemy import text
    from psycopg import sql
    from app.workspace.database import verify_runtime_database
    owner, runtime, probe_role, auxiliary_owner = postgres_runtime_probe
    verify_runtime_database(runtime)
    with owner.begin() as connection:
        if misconfiguration == "no_force":
            connection.execute(text("ALTER TABLE comparison_runs NO FORCE ROW LEVEL SECURITY"))
        elif misconfiguration == "no_rls":
            connection.execute(text("ALTER TABLE requirements DISABLE ROW LEVEL SECURITY"))
        elif misconfiguration == "owner_membership":
            raw = connection.connection.driver_connection
            raw.execute(sql.SQL("ALTER TABLE projects OWNER TO {}").format(sql.Identifier(auxiliary_owner)))
            raw.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(auxiliary_owner), sql.Identifier(probe_role)))
        else:
            connection.execute(text("UPDATE alembic_version SET version_num = '0001_workspace'"))
    expected = "migration" if misconfiguration == "stale_revision" else "restricted" if misconfiguration == "owner_membership" else "RLS"
    with pytest.raises(RuntimeError, match=expected):
        verify_runtime_database(runtime)


@pytest.mark.parametrize("field,expression", [
    ("file_hash", "repeat('b', 64)"), ("object_key", "'other/key'"), ("size", "size + 1"),
    ("mime_type", "'image/png'"), ("name", "'other.pdf'"), ("package_id", ":alternate_package"),
    ("organization_id", ":alternate_org"), ("id", ":alternate_id"),
    ("created_at", "created_at + interval '1 second'"), ("delete", None),
])
def test_postgresql_original_document_provenance_rejects_raw_tampering(postgres_workspace, field, expression):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.workspace.database import tenant_transaction
    from app.workspace.models import Document, PackageRevision
    from app.workspace.repository import Repository
    runtime, org, other_org, actor, _ = postgres_workspace
    with tenant_transaction(runtime, org) as session:
        project = Repository(session, org, actor).create_project("buyer", "Document provenance")
        manifest = {"files": [{"file_hash": "a" * 64, "object_key": "original/key", "size": 10,
                               "mime_type": "application/pdf", "name": "original.pdf"}]}
        package = PackageRevision(organization_id=org, project_id=project.id, kind="tender", version=1, manifest=manifest)
        alternate = PackageRevision(organization_id=org, project_id=project.id, kind="tender", version=2, manifest={"files": []})
        session.add_all([package, alternate])
        session.flush()
        document = Document(organization_id=org, package_id=package.id, **manifest["files"][0])
        session.add(document)
        session.flush()
        parameters = {"id": document.id, "alternate_package": alternate.id, "alternate_org": other_org,
                      "alternate_id": str(uuid4())}
        package_id = package.id
    statement = "DELETE FROM documents WHERE id = :id" if field == "delete" else f"UPDATE documents SET {field} = {expression} WHERE id = :id"
    with pytest.raises(IntegrityError) as rejection, tenant_transaction(runtime, org) as session:
        session.execute(text(statement), parameters)
    assert rejection.value.orig.sqlstate == "23514"
    with tenant_transaction(runtime, org) as session:
        document = session.get(Document, parameters["id"])
        assert document.file_hash == "a" * 64
        assert document.package_id == package_id
        assert session.get(PackageRevision, package_id).manifest == manifest


def test_postgresql_package_created_at_is_immutable_but_processing_status_can_advance(postgres_workspace):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.workspace.database import tenant_transaction
    from app.workspace.models import PackageRevision
    from app.workspace.repository import Repository
    runtime, org, _, actor, _ = postgres_workspace
    with tenant_transaction(runtime, org) as session:
        project = Repository(session, org, actor).create_project("buyer", "Package provenance")
        package = PackageRevision(organization_id=org, project_id=project.id, kind="tender", version=1, manifest={"files": []})
        session.add(package)
        session.flush()
        package_id = package.id
        created_at = package.created_at
        package.status = "processing"
    with pytest.raises(IntegrityError) as rejection, tenant_transaction(runtime, org) as session:
        session.execute(text("UPDATE package_revisions SET created_at = created_at + interval '1 second' WHERE id = :id"), {"id": package_id})
    assert rejection.value.orig.sqlstate == "23514"
    with tenant_transaction(runtime, org) as session:
        package = session.get(PackageRevision, package_id)
        assert package.created_at == created_at
        assert package.status == "processing"
