import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from . import ai
from .db import BUSY, Store
from .evidence import validate_requirements
from .export import matrix_xlsx
from .parsers import SUPPORTED
from .schemas import Requirement
from .tasks import run_comparison, run_extraction

MAX_FILE_BYTES = 10 * 1024 * 1024


async def save_files(store: Store, uploads: list[UploadFile]) -> list[dict]:
    if not 1 <= len(uploads) <= 5:
        raise HTTPException(422, "В пакете должно быть от 1 до 5 файлов")
    for upload in uploads:
        if Path(upload.filename or "").suffix.lower() not in SUPPORTED:
            raise HTTPException(422, "Поддерживаются только текстовые PDF, DOCX и XLSX")
    directory = store.root / "uploads" / uuid4().hex
    directory.mkdir(parents=True)
    saved = []
    paths = []
    try:
        for upload in uploads:
            filename = (upload.filename or "document").replace("\\", "/").split("/")[-1]
            file_id = uuid4().hex
            path = directory / (file_id + Path(filename).suffix.lower())
            paths.append(path)
            size = 0
            with path.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise HTTPException(413, f"«{filename}»: файл превышает 10 МБ")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, f"«{filename}»: пустой файл")
            saved.append({"id": file_id, "name": filename, "path": str(path), "size": size})
        return saved
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    finally:
        for upload in uploads:
            await upload.close()


def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(data_dir or Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        yield

    app = FastAPI(title="Тендер-Сверка", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])

    def require_check(check_id: str) -> dict:
        check = store.get(check_id)
        if check is None:
            raise HTTPException(404, "Проверка не найдена")
        return check

    def claim(check_id: str, stage: str):
        if not store.claim(check_id, stage):
            raise HTTPException(409, "Проверка уже обрабатывается. Дождитесь завершения.")

    @app.get("/health")
    def health():
        return {"status": "ok", "user": "demo", "ai_mode": ai.mode(), "model": os.getenv("OPENAI_MODEL", ai.MODEL),
                "provider": ai.provider_name(), "api_style": ai.api_style()}

    @app.post("/checks", status_code=202)
    async def create_check(background_tasks: BackgroundTasks, files: list[UploadFile]):
        saved = await save_files(store, files)
        check_id = store.create(saved)
        claim(check_id, "extract")
        background_tasks.add_task(run_extraction, check_id, store)
        return {"id": check_id, "status": "queued"}

    @app.get("/checks/{check_id}")
    def get_check(check_id: str):
        check = require_check(check_id)
        check["files"] = [{k: v for k, v in f.items() if k != "path"} for f in check["files"]]
        for bid in check["bids"].values():
            bid["files"] = [{k: v for k, v in f.items() if k != "path"} for f in bid["files"]]
        return check

    @app.put("/checks/{check_id}/requirements")
    def save_requirements(check_id: str, requirements: list[Requirement]):
        check = require_check(check_id)
        if check["status"] in BUSY:
            raise HTTPException(409, "Дождитесь завершения обработки")
        try:
            validate_requirements(requirements, check["blocks"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        claim(check_id, "edit")
        store.update(check_id, requirements=[r.model_dump() for r in requirements],
                     approved=True, results={}, status="done", stage="extract", error=None)
        return {"approved": True}

    @app.put("/checks/{check_id}/bids/{slot}")
    async def save_bid(check_id: str, slot: int, files: list[UploadFile], name: str = Form(...)):
        check = require_check(check_id)
        if slot not in (1, 2, 3):
            raise HTTPException(422, "Допустимы только слоты 1, 2 и 3")
        if not name.strip() or len(name) > 120:
            raise HTTPException(422, "Укажите название участника до 120 символов")
        if not check["approved"]:
            raise HTTPException(409, "Сначала подтвердите чек-лист требований")
        claim(check_id, "upload")
        try:
            saved = await save_files(store, files)
            bids = check["bids"]
            bids[str(slot)] = {"name": name.strip(), "files": saved, "blocks": []}
            store.update(check_id, bids=bids, results={}, status="done", stage="compare", error=None)
        except Exception:
            store.update(check_id, status=check["status"], stage=check["stage"], error=check["error"])
            raise
        return {"slot": slot, "name": name.strip()}

    @app.post("/checks/{check_id}/compare", status_code=202)
    def compare(check_id: str, background_tasks: BackgroundTasks):
        check = require_check(check_id)
        if check["status"] in BUSY:
            raise HTTPException(409, "Сравнение уже выполняется")
        if not check["approved"] or not check["bids"]:
            raise HTTPException(422, "Подтвердите чек-лист и загрузите хотя бы одну заявку")
        claim(check_id, "compare")
        background_tasks.add_task(run_comparison, check_id, store)
        return {"id": check_id, "status": "queued"}

    @app.post("/checks/{check_id}/retry", status_code=202)
    def retry(check_id: str, background_tasks: BackgroundTasks):
        check = require_check(check_id)
        if check["status"] != "failed" or check["stage"] not in {"extract", "compare"}:
            raise HTTPException(409, "Нет неуспешной задачи для повторного запуска")
        claim(check_id, check["stage"])
        task = run_extraction if check["stage"] == "extract" else run_comparison
        background_tasks.add_task(task, check_id, store)
        return {"id": check_id, "status": "queued"}

    @app.get("/checks/{check_id}/matrix.xlsx")
    def export(check_id: str):
        check = require_check(check_id)
        if check["status"] != "done" or not check["results"] or len(check["results"]) != len(check["bids"]):
            raise HTTPException(409, "Дождитесь полного сравнения всех заявок")
        return Response(matrix_xlsx(check),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="tender-{check_id[:8]}.xlsx"'})

    return app


app = create_app()
