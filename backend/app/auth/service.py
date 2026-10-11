"""SQL-serialized identity operations; denied attempts commit before returning."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.workspace.models import AuthBudget, AuthSession, OtpChallenge, User
from .sms import SmsUnavailable


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    def __init__(self, engine, settings, sender, clock):
        self.engine, self.settings, self.sender, self.clock = engine, settings, sender, clock
        self.secret = settings.session_secret.get_secret_value().encode()

    def mac(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def csrf(self, raw):
        return self.mac("csrf:" + raw)

    def _insert(self, model, **values):
        factory = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        return factory(model).values(**values).on_conflict_do_nothing()

    def request_code(self, phone, ip):
        now = self.clock()
        challenge_id, code = str(uuid4()), f"{secrets.randbelow(1_000_000):06d}"
        with Session(self.engine) as db, db.begin():
            budgets = []
            # Stable lock order, durable upserts: concurrent processes share quotas.
            for key, limit in [("ip:" + digest(ip), 20), ("phone:" + phone, 5)]:
                db.execute(self._insert(AuthBudget, id=str(uuid4()), created_at=now, key=key, events=[]))
                budget = db.scalar(select(AuthBudget).where(AuthBudget.key == key).with_for_update())
                events = [stamp for stamp in budget.events if stamp > (now - timedelta(hours=1)).timestamp()]
                if len(events) >= limit or (key.startswith("phone:") and events and now.timestamp() - events[-1] < 60):
                    raise HTTPException(429, "Слишком много запросов. Повторите позже.")
                budgets.append((budget, events))
            for budget, events in budgets:
                budget.events = events + [now.timestamp()]
            db.execute(update(OtpChallenge).where(OtpChallenge.phone == phone, OtpChallenge.consumed.is_(False)).values(consumed=True))
            db.add(OtpChallenge(id=challenge_id, created_at=now, phone=phone,
                code_digest=self.mac(f"otp:{challenge_id}:{phone}:{code}"), expires_at=now + timedelta(minutes=5)))
        try:
            self.sender.send(phone, code)
        except SmsUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception:
            raise HTTPException(503, "Не удалось отправить SMS. Повторите позже.") from None
        with Session(self.engine) as db, db.begin():
            db.execute(update(OtpChallenge).where(OtpChallenge.id == challenge_id, OtpChallenge.consumed.is_(False)).values(delivered=True))
        return {"challenge_id": challenge_id, "expires_in": 300}

    def verify(self, challenge_id, code):
        now, raw = self.clock(), secrets.token_urlsafe(32)
        allowed = False
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            challenge = db.scalar(select(OtpChallenge).where(OtpChallenge.id == challenge_id).with_for_update())
            if challenge and challenge.delivered and not challenge.consumed and challenge.attempts < 5 and challenge.expires_at > now:
                if hmac.compare_digest(challenge.code_digest, self.mac(f"otp:{challenge.id}:{challenge.phone}:{code}")):
                    challenge.consumed = True
                    db.execute(self._insert(User, id=str(uuid4()), created_at=now, phone=challenge.phone, active=True))
                    user = db.scalar(select(User).where(User.phone == challenge.phone).with_for_update())
                    if user.active:
                        db.add(AuthSession(verifier=digest(raw), user_id=user.id, created_at=now,
                            expires_at=now + timedelta(hours=24), last_seen_at=now))
                        allowed = True
                else:
                    challenge.attempts += 1
        if not allowed:
            raise HTTPException(401, "Код недействителен или истёк.")
        return raw, user

    def authenticate(self, raw):
        if not raw or len(raw) > 128:
            raise HTTPException(401, "Требуется вход.")
        now = self.clock()
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            session = db.scalar(select(AuthSession).where(AuthSession.verifier == digest(raw)).with_for_update())
            if not session or session.revoked or session.expires_at <= now or session.last_seen_at + timedelta(minutes=30) <= now:
                raise HTTPException(401, "Сессия недействительна. Войдите снова.")
            user = db.get(User, session.user_id)
            if not user or not user.active:
                raise HTTPException(401, "Сессия недействительна. Войдите снова.")
            session.last_seen_at = now
            return user

    def logout(self, raw):
        with Session(self.engine) as db, db.begin():
            db.execute(update(AuthSession).where(AuthSession.verifier == digest(raw)).values(revoked=True))
