"""Tenant-scoped operations. The caller owns commit/rollback of the transaction.

Project row locks serialize version allocation, slot allocation and job idempotency
on PostgreSQL. No method commits separately from its audit/outbox records.
"""
import copy
import hashlib
import json
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select

from .models import (AuditEvent, ChecklistVersion, Job, OutboxEvent, Participant,
                     Project, Requirement, utcnow)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class ValidationError(Exception):
    pass


REQUIREMENT_FIELDS = ("logical_id", "display_id", "scope", "item", "category", "parameter",
                      "condition", "importance", "predicate", "citations", "origin")


def canonical_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Payload must contain valid JSON values") from exc


def uuid_string(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Expected a UUID") from exc


def snapshot(model):
    return {column.name: (value.isoformat() if hasattr(value, "isoformat") else copy.deepcopy(value))
            for column in model.__table__.columns
            for value in [getattr(model, column.name)]}


class Repository:
    def __init__(self, session, organization_id: str, actor_id: str):
        self.session = session
        self.organization_id = uuid_string(organization_id)
        self.actor_id = uuid_string(actor_id)

    def _query(self, model):
        return select(model).where(model.organization_id == self.organization_id)

    def _get(self, model, entity_id, *, lock=False):
        # Invalid identifiers are indistinguishable from missing tenant resources.
        statement = self._query(model).where(model.id == str(entity_id))
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound(f"{model.__name__} not found")
        return row

    def _audit(self, action, entity, *, project_id=None, before=None, after=None, reason=None):
        self.session.add(AuditEvent(organization_id=self.organization_id, actor_id=self.actor_id,
            project_id=project_id, action=action, entity_type=entity.__tablename__, entity_id=entity.id,
            before=copy.deepcopy(before), after=copy.deepcopy(after), reason=reason))
        self.session.flush()

    def create_project(self, mode, lot_name, customer=None, deadline=None,
                       timezone="Asia/Tashkent", report_language="ru"):
        if mode not in {"supplier", "buyer"}:
            raise ValidationError("Mode must be supplier or buyer")
        if not isinstance(lot_name, str) or not lot_name.strip():
            raise ValidationError("Lot name is required")
        if deadline is not None and deadline.tzinfo is None:
            raise ValidationError("Deadline must include a timezone")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValidationError("Timezone must be an IANA timezone") from exc
        if report_language not in {"ru", "uz", "en"}:
            raise ValidationError("Report language must be ru, uz or en")
        project = Project(organization_id=self.organization_id, mode=mode, lot_name=lot_name.strip(),
                          customer=customer, deadline=deadline, timezone=timezone, report_language=report_language)
        self.session.add(project)
        self.session.flush()
        self._audit("project.created", project, project_id=project.id, after=snapshot(project))
        return project

    def get_project(self, project_id):
        return self._get(Project, project_id)

    def list_projects(self):
        return list(self.session.scalars(self._query(Project).order_by(Project.created_at, Project.id)))

    def _normalize_requirements(self, requirements):
        if not isinstance(requirements, list):
            raise ValidationError("Requirements must be a list")
        values = []
        logical_ids, display_ids = set(), set()
        for raw in requirements:
            if not isinstance(raw, dict) or set(raw) - set(REQUIREMENT_FIELDS):
                raise ValidationError("Unknown requirement fields")
            row = {**raw}
            row["logical_id"] = uuid_string(row.get("logical_id", str(uuid4())))
            for name in ("display_id", "scope", "category", "importance"):
                if not isinstance(row.get(name), str) or not row[name].strip():
                    raise ValidationError(f"Requirement {name} is required")
            if row["scope"] not in {"product", "participant"}:
                raise ValidationError("Requirement scope must be product or participant")
            if row["importance"] not in {"mandatory", "scored", "desirable"}:
                raise ValidationError("Requirement importance must be mandatory, scored or desirable")
            row.setdefault("item", None)
            if row["item"] is not None and not isinstance(row["item"], str):
                raise ValidationError("Requirement item must be text or null")
            if not re.fullmatch(r"[TU]-0*[1-9][0-9]*", row["display_id"]):
                raise ValidationError("Requirement display ID must use T- or U- followed by a positive number")
            if row["logical_id"] in logical_ids or row["display_id"] in display_ids:
                raise ValidationError("Requirement logical and display IDs must be unique within a checklist")
            logical_ids.add(row["logical_id"])
            display_ids.add(row["display_id"])
            for name in ("parameter", "condition"):
                row.setdefault(name, None)
                if row[name] is not None and not isinstance(row[name], str):
                    raise ValidationError(f"Requirement {name} must be text")
            for name, default, kind in (("predicate", {}, dict), ("citations", [], list), ("origin", {}, dict)):
                row.setdefault(name, default)
                if not isinstance(row[name], kind):
                    raise ValidationError(f"Requirement {name} has invalid shape")
            canonical_json(row)
            values.append(copy.deepcopy(row))
        return values

    def _insert_requirements(self, checklist, values):
        self.session.add_all([Requirement(organization_id=self.organization_id, checklist_id=checklist.id, **row)
                              for row in values])
        self.session.flush()

    def create_checklist_draft(self, project_id, requirements=None):
        project = self._get(Project, project_id, lock=True)
        values = self._normalize_requirements([] if requirements is None else requirements)
        version = self.session.scalar(select(func.max(ChecklistVersion.version)).where(
            ChecklistVersion.organization_id == self.organization_id, ChecklistVersion.project_id == project.id)) or 0
        draft = ChecklistVersion(organization_id=self.organization_id, project_id=project.id, version=version + 1)
        self.session.add(draft)
        self.session.flush()
        self._insert_requirements(draft, values)
        self._audit("checklist.created", draft, project_id=project.id, after=self._checklist_snapshot(draft))
        return draft

    def get_checklist(self, checklist_id):
        return self._get(ChecklistVersion, checklist_id)

    def list_requirements(self, checklist_id):
        self.get_checklist(checklist_id)
        return list(self.session.scalars(self._query(Requirement).where(Requirement.checklist_id == checklist_id)
                                        .order_by(Requirement.display_id)))

    def list_checklist_history(self, project_id):
        self.get_project(project_id)
        return list(self.session.scalars(self._query(ChecklistVersion).where(ChecklistVersion.project_id == project_id)
                                        .order_by(ChecklistVersion.version)))

    def _checklist_snapshot(self, checklist):
        return {**snapshot(checklist), "requirements": [snapshot(row) for row in self.list_requirements(checklist.id)]}

    def _locked_checklist(self, checklist_id):
        checklist = self.get_checklist(checklist_id)
        self._get(Project, checklist.project_id, lock=True)
        return self._get(ChecklistVersion, checklist_id, lock=True)

    def _require_draft_revision(self, checklist, expected_revision):
        if checklist.status != "draft":
            raise Conflict("Approved checklists are immutable; fork a draft to edit")
        if checklist.revision != expected_revision:
            raise Conflict("Checklist revision changed")

    def save_checklist_draft(self, checklist_id, requirements, expected_revision, reason=None):
        checklist = self._locked_checklist(checklist_id)
        self._require_draft_revision(checklist, expected_revision)
        values = self._normalize_requirements(requirements)
        before = self._checklist_snapshot(checklist)
        old = {row.logical_id: {key: copy.deepcopy(getattr(row, key)) for key in REQUIREMENT_FIELDS}
               for row in self.list_requirements(checklist.id)}
        new = {row["logical_id"]: row for row in values}
        editing = any(key not in new or canonical_json(row) != canonical_json(new[key]) for key, row in old.items())
        if editing and (not isinstance(reason, str) or not reason.strip()):
            raise ValidationError("A reason is required when editing or deleting existing requirements")
        self.session.execute(delete(Requirement).where(Requirement.organization_id == self.organization_id,
                                                      Requirement.checklist_id == checklist.id))
        self._insert_requirements(checklist, values)
        checklist.revision += 1
        self.session.flush()
        self._audit("checklist.saved", checklist, project_id=checklist.project_id,
                    before=before, after=self._checklist_snapshot(checklist), reason=reason)
        return checklist

    def approve_checklist(self, checklist_id, expected_revision):
        checklist = self._locked_checklist(checklist_id)
        self._require_draft_revision(checklist, expected_revision)
        requirements = self.list_requirements(checklist.id)
        if not requirements:
            raise ValidationError("An empty checklist cannot be approved")
        for row in requirements:
            for field in ("parameter", "condition"):
                if not getattr(row, field) or not getattr(row, field).strip():
                    raise ValidationError(f"Requirement {row.display_id} needs a nonempty {field} before approval")
        before = self._checklist_snapshot(checklist)
        checklist.status = "approved"
        checklist.approved_by = self.actor_id
        checklist.approved_at = utcnow()
        checklist.revision += 1
        self.session.flush()
        self._audit("checklist.approved", checklist, project_id=checklist.project_id,
                    before=before, after=self._checklist_snapshot(checklist))
        return checklist

    def fork_checklist(self, checklist_id):
        original = self._locked_checklist(checklist_id)
        if original.status != "approved":
            raise Conflict("Only an approved checklist can be forked")
        values = []
        for row in self.list_requirements(original.id):
            value = {key: copy.deepcopy(getattr(row, key)) for key in REQUIREMENT_FIELDS}
            value["origin"] = {**value["origin"], "parent_checklist_id": original.id, "parent_requirement_id": row.id}
            values.append(value)
        draft = self.create_checklist_draft(original.project_id, values)
        draft.parent_version_id = original.id
        self.session.flush()
        self._audit("checklist.forked", draft, project_id=original.project_id,
                    before={"source_checklist_id": original.id}, after=self._checklist_snapshot(draft))
        return draft

    def create_participant(self, project_id, name, slot):
        project = self._get(Project, project_id, lock=True)
        limit = 1 if project.mode == "supplier" else 5
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= limit:
            raise ValidationError(f"Participant slot must be between 1 and {limit}")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Participant name is required")
        if self.session.scalar(self._query(Participant).where(Participant.project_id == project.id, Participant.slot == slot)):
            raise Conflict("Participant slot is already occupied")
        participant = Participant(organization_id=self.organization_id, project_id=project.id, name=name.strip(), slot=slot)
        self.session.add(participant)
        self.session.flush()
        self._audit("participant.created", participant, project_id=project.id, after=snapshot(participant))
        return participant

    def get_participant(self, participant_id):
        return self._get(Participant, participant_id)

    def list_participants(self, project_id):
        self.get_project(project_id)
        return list(self.session.scalars(self._query(Participant).where(Participant.project_id == project_id).order_by(Participant.slot)))

    def create_job(self, stage, project_id, input_payload, idempotency_key):
        project = self._get(Project, project_id, lock=True)
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 64:
            raise ValidationError("Job stage is required and must fit 64 characters")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValidationError("Idempotency key is required and must fit 255 characters")
        if not isinstance(input_payload, dict):
            raise ValidationError("Job input payload must be an object")
        input_hash = hashlib.sha256(canonical_json({"stage": stage, "payload": input_payload}).encode("utf-8")).hexdigest()
        existing = self.session.scalar(self._query(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key))
        if existing:
            if existing.input_hash != input_hash:
                raise Conflict("Idempotency key was already used for different job input")
            return existing
        job = Job(organization_id=self.organization_id, project_id=project.id, stage=stage,
                  input_hash=input_hash, idempotency_key=idempotency_key, payload=copy.deepcopy(input_payload))
        self.session.add(job)
        self.session.flush()
        self.session.add(OutboxEvent(organization_id=self.organization_id, job_id=job.id,
                                   payload={"job_id": job.id, "organization_id": self.organization_id, "stage": stage}))
        self._audit("job.created", job, project_id=project.id, after=snapshot(job))
        return job

    def get_job(self, job_id):
        return self._get(Job, job_id)

    def list_jobs(self, project_id):
        self.get_project(project_id)
        return list(self.session.scalars(self._query(Job).where(Job.project_id == project_id).order_by(Job.created_at, Job.id)))

    def list_audit(self, project_id=None):
        statement = self._query(AuditEvent)
        if project_id is not None:
            self.get_project(project_id)
            statement = statement.where(AuditEvent.project_id == project_id)
        return list(self.session.scalars(statement.order_by(AuditEvent.created_at, AuditEvent.id)))
