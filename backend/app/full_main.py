"""Full authenticated application. Start with uvicorn app.full_main:create_app --factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.auth.routes import router as auth_router
from app.auth.service import AuthService
from app.auth.sms import HttpSmsSender, SmsSenderProtocol
from app.config import Settings
from app.workspace.database import create_engine, verify_runtime_database
from app.workspace.models import utcnow
from app.workspace.repository import Conflict, NotFound, ValidationError


def create_app(settings=None, *, engine=None, sms_sender: SmsSenderProtocol | None = None, clock=utcnow):
    settings = settings or Settings()
    if settings.environment != "test" and (engine is not None or sms_sender is not None or clock is not utcnow):
        raise ValueError("Dependency injection requires the explicit test environment")
    owns_engine = engine is None
    engine = engine or create_engine(settings.database_url.get_secret_value(),
        allow_sqlite_for_tests=settings.environment == "test")

    @asynccontextmanager
    async def lifespan(app):
        verify_runtime_database(engine, allow_sqlite_for_tests=settings.environment == "test")
        yield
        if owns_engine:
            engine.dispose()

    app = FastAPI(title="Tender workspace", lifespan=lifespan)
    app.state.settings, app.state.engine = settings, engine
    app.state.cookie_secure = settings.public_origin.startswith("https://")
    app.state.cookie_name = "__Host-tender_session" if app.state.cookie_secure else "tender_session"
    app.state.auth = AuthService(engine, settings,
        sms_sender or HttpSmsSender(settings.sms_endpoint, settings.sms_token), clock)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.public_origin],
        allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"])

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        # Validation errors normally echo input; expose neither values nor internals.
        return JSONResponse(status_code=422, content={"detail": "Неверные данные запроса."})

    for error, status, message in [(NotFound, 404, "Ресурс не найден."),
        (Conflict, 409, "Конфликт изменений. Обновите данные и повторите."),
        (ValidationError, 422, "Недопустимые данные или состояние ресурса."),
        (SQLAlchemyError, 503, "Сервис временно недоступен.")]:
        async def safe_error(request, exc, status=status, message=message):
            return JSONResponse(status_code=status, content={"detail": message})
        app.add_exception_handler(error, safe_error)

    app.include_router(auth_router)
    from app.full_workspace.routes import router as workspace_router
    app.include_router(workspace_router)
    return app
