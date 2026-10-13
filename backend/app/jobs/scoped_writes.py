"""Explicit project-scoped domain records; callbacks receive no SQL or Session."""
import copy
from datetime import datetime
from uuid import uuid4

from sqlalchemy import event, select

from app.workspace.models import (Project, Participant, PackageRevision, Document,
    ChecklistVersion, Requirement, ComparisonRun, PackageInspection, NormalizedArtifact, ArtifactUploadAttempt, utcnow)
from app.workspace.repository import ValidationError, canonical_json, uuid_string

DOMAIN_MODELS = {Project, Participant, PackageRevision, Document, ChecklistVersion, Requirement, ComparisonRun, PackageInspection, NormalizedArtifact, ArtifactUploadAttempt}
DOMAIN_ORDER = (Project, Participant, PackageRevision, ChecklistVersion, Document, Requirement, ComparisonRun, PackageInspection, NormalizedArtifact, ArtifactUploadAttempt)
IDENTITY_FIELDS = {"id", "organization_id", "project_id", "package_id", "checklist_id",
                   "package_ids", "participant_id", "parent_version_id", "job_id", "document_id", "attempt_id", "artifact_id", "actor_id"}


class ScopeViolation(ValidationError):
    """A callback attempted to leave its claimed job's domain scope."""


class FencedWrites:
    """Detached mutable records copied to scoped ORM rows at flush.

    get/all qualify every read by organization and the job's project, including
    document/package and requirement/checklist ancestry. add/delete/flush are
    the only write operations. Scope identity cannot be reassigned on a record.
    New pipeline models must be explicitly added to this domain allowlist.
    """
    def __init__(self, session, organization_id, project_id, job_id=None):
        self.__session, self.__organization, self.__project = session, organization_id, project_id
        self.__records = {}
        self.__deleted = set()
        self.__job = job_id

    def _model(self, model):
        if model not in DOMAIN_MODELS:
            raise ScopeViolation()
        return model

    def _query(self, model):
        self._model(model)
        query = select(model).where(model.organization_id == self.__organization)
        if model is Project:
            return query.where(Project.id == self.__project)
        if model is Document:
            return query.join(PackageRevision, (Document.package_id == PackageRevision.id) &
                (Document.organization_id == PackageRevision.organization_id)).where(PackageRevision.project_id == self.__project)
        if model is Requirement:
            return query.join(ChecklistVersion, (Requirement.checklist_id == ChecklistVersion.id) &
                (Requirement.organization_id == ChecklistVersion.organization_id)).where(ChecklistVersion.project_id == self.__project)
        return query.where(model.project_id == self.__project)

    def _values(self, row):
        values = {column.name: copy.deepcopy(getattr(row, column.name)) for column in row.__table__.columns}
        for value in values.values():
            if hasattr(value, "_compiler_dispatch"):
                raise ScopeViolation()
            if isinstance(value, (dict, list)):
                canonical_json(value)
        return values

    def _check_scope(self, row):
        model = self._model(type(row))
        self._values(row)  # Reject expressions before an ancestry query can run.
        if row.organization_id != self.__organization:
            raise ScopeViolation()
        if model is Project:
            valid = row.id == self.__project
        elif model in {Document, Requirement}:
            ancestor = PackageRevision if model is Document else ChecklistVersion
            parent_id = row.package_id if model is Document else row.checklist_id
            self._require_parent(ancestor, parent_id)
            valid = True
        else:
            valid = row.project_id == self.__project
        if not valid:
            raise ScopeViolation()
        if model is PackageRevision and row.participant_id is not None:
            self._require_parent(Participant, row.participant_id)
        if model is ChecklistVersion and row.parent_version_id is not None:
            self._require_parent(ChecklistVersion, row.parent_version_id)
        if model is ComparisonRun:
            self._require_parent(ChecklistVersion, row.checklist_id)
            if not isinstance(row.package_ids, list):
                raise ScopeViolation()
            for package_id in row.package_ids:
                self._require_parent(PackageRevision, package_id)
        if model is PackageInspection:
            self._require_parent(PackageRevision,row.package_id)
        if model in {NormalizedArtifact, ArtifactUploadAttempt}:
            if self.__job is None or row.job_id != self.__job:
                raise ScopeViolation()
            self._require_parent(PackageRevision, row.package_id)
            with self.__session.no_autoflush:
                document = self.__session.scalar(self._query(Document).where(Document.id == row.document_id,
                    Document.package_id == row.package_id))
            if document is None:
                raise ScopeViolation()
            if model is NormalizedArtifact:
                self._require_parent(ArtifactUploadAttempt, row.attempt_id)

    def _require_parent(self, model, identity):
        # Validate JSON references as rigorously as relational parent columns.
        # A staged parent may be created in this same fenced transaction.
        if not isinstance(identity, str):
            raise ScopeViolation()
        identity = uuid_string(identity)
        key = (model, identity)
        staged = self.__records.get(key)
        if staged is not None:
            parent = staged[0]
            self._values(parent)
            if key in self.__deleted or parent.organization_id != self.__organization or parent.project_id != self.__project:
                raise ScopeViolation()
            return
        with self.__session.no_autoflush:
            exists = self.__session.scalar(self._query(model).where(model.id == identity))
        if exists is None:
            raise ScopeViolation()

    def _record(self, real):
        self._check_scope(real)
        key = (type(real), real.id)
        if key not in self.__records:
            values = self._values(real)
            clone = type(real)(**copy.deepcopy(values))
            self.__records[key] = [clone, real, values]
        return self.__records[key][0]

    def get(self, model, identity):
        self._model(model)
        identity = uuid_string(identity)
        with self.__session.no_autoflush:
            row = self.__session.scalar(self._query(model).where(model.id == identity))
        if row is None:
            raise ScopeViolation()
        return self._record(row)

    def all(self, model, **filters):
        query = self._query(model)
        for name, value in filters.items():
            if name not in model.__table__.columns or not (value is None or type(value) in {str, int, bool, float, datetime}):
                raise ScopeViolation()
            query = query.where(getattr(model, name) == value)
        with self.__session.no_autoflush:
            rows = list(self.__session.scalars(query.order_by(model.id)))
        return [self._record(row) for row in rows]

    def add(self, row):
        self._model(type(row))
        row.id = row.id or str(uuid4())
        row.created_at = row.created_at or utcnow()
        self._check_scope(row)
        key = (type(row), row.id)
        if key in self.__records:
            if self.__records[key][0] is not row:
                raise ScopeViolation()
            return
        self.__records[key] = [row, None, self._values(row)]

    def add_all(self, rows):
        for row in rows:
            self.add(row)

    def delete(self, row):
        self._check_scope(row)
        key = (type(row), row.id)
        if key not in self.__records or self.__records[key][0] is not row:
            raise ScopeViolation()
        self.__deleted.add(key)

    def _validate_pending(self, session, *args):
        for row in session.new | session.dirty | session.deleted:
            self._check_scope(row)

    def flush(self):
        # Validate every detached dirty/new/deleted record before staging any SQL.
        for key, (clone, real, original) in self.__records.items():
            self._check_scope(clone)
            values = self._values(clone)
            if any(values.get(field) != original.get(field) for field in IDENTITY_FIELDS if field in values):
                raise ScopeViolation()
        # No ORM relationships expose live sessions to callbacks. Flush explicit
        # ancestry tiers instead, while retaining one enclosing fenced transaction.
        for model in DOMAIN_ORDER:
            for key, entry in self.__records.items():
                if key[0] is not model or key in self.__deleted:
                    continue
                clone, real, original = entry
                values = self._values(clone)
                if real is None:
                    real = type(clone)(**{name: value for name, value in values.items() if value is not None})
                    entry[1] = real
                    self.__session.add(real)
                else:
                    for name, value in values.items():
                        if value != original[name]:
                            setattr(real, name, copy.deepcopy(value))
            self._validate_pending(self.__session)
            self.__session.flush()
        for model in reversed(DOMAIN_ORDER):
            for key in self.__deleted:
                real = self.__records[key][1]
                if key[0] is model and real is not None:
                    self.__session.delete(real)
            self._validate_pending(self.__session)
            self.__session.flush()
        for key, entry in list(self.__records.items()):
            if key in self.__deleted:
                del self.__records[key]
                self.__deleted.remove(key)
                continue
            clone, real, original = entry
            values = self._values(real)
            for name, value in values.items():
                setattr(clone, name, copy.deepcopy(value))
            entry[2] = values

    def execute(self, *args, **kwargs):
        raise ScopeViolation()

    scalar = scalars = execute

    def commit(self):
        raise RuntimeError("Transaction is owned by the lease fence")


def fenced_callback(db, callback, organization_id, project_id, job_id=None):
    writes = FencedWrites(db, organization_id, project_id, job_id)
    def reject_commit(session):
        raise RuntimeError("Transaction is owned by the lease fence")
    event.listen(db, "before_commit", reject_commit)
    event.listen(db, "before_flush", writes._validate_pending)
    try:
        callback(writes)
        writes.flush()
    finally:
        event.remove(db, "before_commit", reject_commit)
        event.remove(db, "before_flush", writes._validate_pending)
