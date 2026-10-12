"""Actual PostgreSQL regressions for callback scope and late SQL/commit expiry."""
from datetime import timedelta
import time
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update

from test_jobs_execution import jobs
from app.jobs.service import JobService, LeaseLost
from app.workspace.database import tenant_transaction
from app.workspace.models import (Job, JobCheckpoint, JobProgressEvent, Organization,
    Project, Participant, PackageRevision, Document, ChecklistVersion, Requirement, ComparisonRun, User, utcnow)
from app.workspace.repository import Repository, ValidationError


@pytest.fixture
def scopes(jobs):
    engine, org, actor, project, job = jobs
    foreign_org = str(uuid4())
    with tenant_transaction(engine, org) as db:
        other = Repository(db, org, actor).create_project("buyer", "Same tenant other project")
        other_project = other.id
        package = PackageRevision(organization_id=org, project_id=project, kind="tender", version=1, manifest={})
        db.add(package)
        db.flush()
        package_id = package.id
    with tenant_transaction(engine, foreign_org) as db:
        db.add(Organization(id=foreign_org, name="Foreign callback target"))
        db.flush()
        foreign = Repository(db, foreign_org, actor).create_project("buyer", "Foreign")
        foreign_project = foreign.id
    return other_project, foreign_org, foreign_project, package_id


@pytest.mark.parametrize("attack", ["same_project", "global_model", "control_model", "context_sql", "bulk_sql", "dirty_scope", "new_scope"])
def test_callback_cannot_escape_claimed_project_or_tenant(jobs, scopes, attack):
    engine, org, actor, project, job = jobs
    other, foreign_org, foreign_project, _ = scopes
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        if attack == "same_project":
            db.get(Project, other).customer = "escaped"
        elif attack == "global_model":
            db.get(User, actor).display_name = "escaped"
        elif attack == "control_model":
            db.get(Job, job).status = "succeeded"
        elif attack == "context_sql":
            db.scalar(select(func.set_config("app.organization_id", foreign_org, True)))
            db.execute(update(Project).where(Project.id == foreign_project).values(customer="escaped"))
            db.scalar(select(func.set_config("app.organization_id", org, True)))
        elif attack == "bulk_sql":
            db.execute(update(Project).where(Project.id == other).values(customer="escaped"))
        elif attack == "dirty_scope":
            db.get(Project, project).organization_id = foreign_org
            db.flush()
        else:
            db.add(Participant(organization_id=org, project_id=other, name="escaped", slot=1))
            db.flush()
    with pytest.raises(ValidationError):
        runner.checkpoint(job, claim.token, attack, "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(Project, other).customer is None
        assert db.scalar(select(func.count()).select_from(JobCheckpoint).where(JobCheckpoint.job_id == job)) == 0
        assert db.get(Job, job).status == "running"
    with tenant_transaction(engine, foreign_org) as db:
        assert db.get(Project, foreign_project).customer is None


def test_scoped_domain_api_supports_detached_records_and_document_package_scope(jobs, scopes):
    from sqlalchemy.orm import object_session
    engine, org, _, project, job = jobs
    other, _, _, package = scopes
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        row = db.get(Project, project)
        assert object_session(row) is None
        row.customer = "scoped output"
        assert len(db.all(Project)) == 1
        assert db.all(Project)[0].id == project
        db.add(Document(organization_id=org, package_id=package, file_hash="0" * 64,
            object_key=str(uuid4()), size=1, mime_type="text/plain", name="fixture"))
        db.flush()
        assert len(db.all(Document, package_id=package)) == 1
    runner.checkpoint(job, claim.token, "usable", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(Project, project).customer == "scoped output"
        assert db.scalar(select(func.count()).select_from(Document).where(Document.package_id == package)) == 1


@pytest.mark.parametrize("model", [Document, Requirement])
def test_detached_domain_parent_cannot_be_reassigned_to_another_project(jobs, scopes, model):
    engine, org, _, project, job = jobs
    other, _, _, package = scopes
    with tenant_transaction(engine, org) as db:
        if model is Document:
            parent = PackageRevision(organization_id=org, project_id=other, kind="tender", version=1, manifest={})
            db.add(parent)
            db.flush()
            row = Document(organization_id=org, package_id=package, file_hash="0" * 64,
                object_key=str(uuid4()), size=1, mime_type="text/plain", name="fixture")
            parent_field = "package_id"
        else:
            original = ChecklistVersion(organization_id=org, project_id=project, version=1)
            parent = ChecklistVersion(organization_id=org, project_id=other, version=1)
            db.add_all([original, parent])
            db.flush()
            row = Requirement(organization_id=org, checklist_id=original.id, display_id="1", scope="product",
                importance="mandatory", category="fixture", parameter="size", condition="one")
            parent_field = "checklist_id"
        db.add(row)
        db.flush()
        identity, original_parent, invalid_parent = row.id, getattr(row, parent_field), parent.id
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        setattr(db.get(model, identity), parent_field, invalid_parent)
        db.flush()
    with pytest.raises(ValidationError):
        runner.checkpoint(job, claim.token, "parent-scope", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert getattr(db.get(model, identity), parent_field) == original_parent


def test_scoped_api_can_create_validated_package_document_and_checklist_requirement_together(jobs):
    engine, org, _, project, job = jobs
    runner = JobService(engine, org)
    claim = runner.claim(job)
    ids = {}
    def callback(db):
        package = PackageRevision(organization_id=org, project_id=project, kind="tender", version=1, manifest={})
        checklist = ChecklistVersion(organization_id=org, project_id=project, version=1)
        db.add_all([package, checklist])
        document = Document(organization_id=org, package_id=package.id, file_hash="0" * 64,
            object_key=str(uuid4()), size=1, mime_type="text/plain", name="fixture")
        requirement = Requirement(organization_id=org, checklist_id=checklist.id, display_id="1", scope="product",
            importance="mandatory", category="fixture", parameter="size", condition="one")
        db.add_all([document, requirement])
        db.flush()
        ids.update(document=document.id, requirement=requirement.id)
        assert package.status == "uploaded" and checklist.status == "draft"
    runner.checkpoint(job, claim.token, "package-and-checklist", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(Document, ids["document"]) and db.get(Requirement, ids["requirement"])


@pytest.mark.parametrize("reference", ["other_project", "foreign_tenant", "missing", "malformed", "wrong_type"])
def test_comparison_package_references_cannot_leave_claimed_scope(jobs, scopes, reference):
    engine, org, _, project, job = jobs
    other, foreign_org, foreign_project, _ = scopes
    with tenant_transaction(engine, org) as db:
        checklist = ChecklistVersion(organization_id=org, project_id=project, version=1)
        package = PackageRevision(organization_id=org, project_id=other, kind="tender", version=1, manifest={})
        db.add_all([checklist, package])
        db.flush()
        checklist_id, other_package = checklist.id, package.id
    with tenant_transaction(engine, foreign_org) as db:
        package = PackageRevision(organization_id=foreign_org, project_id=foreign_project, kind="tender", version=1, manifest={})
        db.add(package)
        db.flush()
        foreign_package = package.id
    referenced = {"other_project": [other_package], "foreign_tenant": [foreign_package],
        "missing": [str(uuid4())], "malformed": ["not-a-uuid"], "wrong_type": other_package}[reference]
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        db.add(ComparisonRun(organization_id=org, project_id=project, checklist_id=checklist_id,
            package_ids=referenced, scoring_policy={}, provider_config_versions={}))
    with pytest.raises(ValidationError):
        runner.checkpoint(job, claim.token, "comparison-input", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.scalar(select(func.count()).select_from(ComparisonRun).where(ComparisonRun.project_id == project)) == 0


@pytest.mark.parametrize("reference", ["participant_id", "parent_version_id", "checklist_id"])
def test_callback_rejects_nonexistent_or_other_project_ancestry_before_sql(jobs, scopes, reference):
    engine, org, _, project, job = jobs
    other, _, _, _ = scopes
    with tenant_transaction(engine, org) as db:
        participant = Participant(organization_id=org, project_id=other, slot=1, name="Other")
        checklist = ChecklistVersion(organization_id=org, project_id=other, version=1)
        db.add_all([participant, checklist])
        db.flush()
        participant_id, checklist_id = participant.id, checklist.id
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        if reference == "participant_id":
            row = PackageRevision(organization_id=org, project_id=project, kind="bid", participant_id=participant_id, version=1, manifest={})
        elif reference == "parent_version_id":
            row = ChecklistVersion(organization_id=org, project_id=project, version=1, parent_version_id=checklist_id)
        else:
            row = ComparisonRun(organization_id=org, project_id=project, checklist_id=checklist_id,
                package_ids=[], scoring_policy={}, provider_config_versions={})
        db.add(row)
    with pytest.raises(ValidationError):
        runner.checkpoint(job, claim.token, reference, "v1", {}, callback)


def test_existing_draft_parent_identity_cannot_be_reassigned(jobs):
    engine, org, _, project, job = jobs
    with tenant_transaction(engine, org) as db:
        first = ChecklistVersion(organization_id=org, project_id=project, version=1)
        second = ChecklistVersion(organization_id=org, project_id=project, version=2)
        db.add_all([first, second])
        db.flush()
        draft = ChecklistVersion(organization_id=org, project_id=project, version=3, parent_version_id=first.id)
        db.add(draft)
        db.flush()
        draft_id, initial_parent, changed_parent = draft.id, first.id, second.id
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        db.get(ChecklistVersion, draft_id).parent_version_id = changed_parent
    with pytest.raises(ValidationError):
        runner.checkpoint(job, claim.token, "draft-parent", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(ChecklistVersion, draft_id).parent_version_id == initial_parent


def test_scoped_references_allow_new_fork_and_exact_project_comparison(jobs):
    engine, org, _, project, job = jobs
    runner = JobService(engine, org)
    claim = runner.claim(job)
    def callback(db):
        participant = Participant(organization_id=org, project_id=project, slot=1, name="Own")
        first = ChecklistVersion(organization_id=org, project_id=project, version=1)
        db.add_all([participant, first])
        fork = ChecklistVersion(organization_id=org, project_id=project, version=2, parent_version_id=first.id)
        package = PackageRevision(organization_id=org, project_id=project, kind="bid", participant_id=participant.id, version=1, manifest={})
        db.add_all([fork, package])
        comparison = ComparisonRun(organization_id=org, project_id=project, checklist_id=fork.id,
            package_ids=[package.id], scoring_policy={}, provider_config_versions={})
        db.add(comparison)
    runner.checkpoint(job, claim.token, "valid-references", "v1", {}, callback)
    with tenant_transaction(engine, org) as db:
        assert db.scalar(select(func.count()).select_from(ComparisonRun).where(ComparisonRun.project_id == project)) == 1


@pytest.mark.parametrize("operation", ["checkpoint", "finish", "fail", "heartbeat", "defer"])
def test_pending_final_sql_cannot_commit_after_original_lease_expiry(jobs, operation):
    engine, org, _, project, job = jobs
    runner = JobService(engine, org, lease_seconds=1)
    claim = runner.claim(job)
    with tenant_transaction(engine, org) as db:
        original_expiry = db.get(Job, job).lease_expires_at
    def delay(connection, cursor, statement, parameters, context, many):
        if (operation == "checkpoint" and statement.startswith("INSERT INTO job_checkpoints")) or \
                (operation != "checkpoint" and statement.startswith("UPDATE jobs")):
            time.sleep(1.2)
    event.listen(engine, "before_cursor_execute", delay)
    try:
        with pytest.raises(LeaseLost):
            if operation == "checkpoint":
                runner.checkpoint(job, claim.token, "late", "v1", {},
                    lambda db: setattr(db.get(Project, project), "customer", "expired result"))
            elif operation == "finish":
                runner.finish(job, claim.token, lambda db: setattr(db.get(Project, project), "customer", "expired result"))
            elif operation == "fail":
                runner.fail(job, claim.token, "handler_failed")
            elif operation == "heartbeat":
                runner.heartbeat(job, claim.token)
            else:
                runner.defer(job, claim.token)
    finally:
        event.remove(engine, "before_cursor_execute", delay)
    with tenant_transaction(engine, org) as db:
        row = db.get(Job, job)
        assert row.status == "running" and row.lease_token == claim.token
        assert row.lease_expires_at == original_expiry
        assert db.get(Project, project).customer is None
        assert db.scalar(select(func.count()).select_from(JobCheckpoint).where(JobCheckpoint.job_id == job)) == 0
        assert db.scalar(select(func.count()).select_from(JobProgressEvent).where(JobProgressEvent.job_id == job)) == 1


@pytest.mark.parametrize("operation", ["checkpoint", "finish", "fail", "heartbeat", "defer"])
def test_database_commit_guard_preserves_original_deadline_during_commit_delay(jobs, operation):
    engine, org, _, project, job = jobs
    runner = JobService(engine, org, lease_seconds=1)
    claim = runner.claim(job)
    def delay_commit(connection):
        time.sleep(1.2)
    event.listen(engine, "commit", delay_commit)
    try:
        with pytest.raises(LeaseLost):
            if operation == "checkpoint":
                runner.checkpoint(job, claim.token, "commit-late", "v1", {},
                    lambda db: setattr(db.get(Project, project), "customer", "must rollback"))
            elif operation == "finish":
                runner.finish(job, claim.token, lambda db: setattr(db.get(Project, project), "customer", "must rollback"))
            elif operation == "fail":
                runner.fail(job, claim.token, "handler_failed")
            elif operation == "heartbeat":
                runner.heartbeat(job, claim.token)
            else:
                runner.defer(job, claim.token)
    finally:
        event.remove(engine, "commit", delay_commit)
    with tenant_transaction(engine, org) as db:
        row = db.get(Job, job)
        assert row.status == "running" and row.lease_token == claim.token
        assert db.get(Project, project).customer is None


@pytest.mark.parametrize("delay_at", ["flush", "commit"])
def test_heartbeat_cannot_use_its_extended_expiry_to_survive_original_deadline(jobs, delay_at):
    engine, org, _, _, job = jobs
    runner = JobService(engine, org, lease_seconds=1)
    claim = runner.claim(job)
    with tenant_transaction(engine, org) as db:
        original = db.get(Job, job).lease_expires_at
    time.sleep(0.7)
    def delayed_sql(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE jobs"):
            time.sleep(0.5)
    def delayed_commit(connection):
        time.sleep(0.5)
    hook, callback = ("before_cursor_execute", delayed_sql) if delay_at == "flush" else ("commit", delayed_commit)
    event.listen(engine, hook, callback)
    try:
        with pytest.raises(LeaseLost):
            runner.heartbeat(job, claim.token)
    finally:
        event.remove(engine, hook, callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).lease_expires_at == original
