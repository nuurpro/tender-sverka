from fastapi.testclient import TestClient

from test_api import docx_bytes

TENDER = "\n".join([
    "УЧЕБНЫЙ КОМПЛЕКТ DEMO-001",
    "Оперативная память — не менее 16 ГБ.",
    "Накопитель — не менее 512 ГБ.",
    "Гарантия — не менее 24 месяцев.",
    "Документы — сертификат соответствия и авторизационное письмо.",
    "Срок поставки — не более 10 дней.",
    "Сервисный центр — на территории Узбекистана.",
])


def test_demo_full_pipeline_and_edited_condition(tmp_path, monkeypatch):
    from app.main import create_app
    monkeypatch.setenv("AI_MODE", "demo")
    with TestClient(create_app(tmp_path)) as client:
        check_id = client.post("/checks", files=[("files", ("tender.docx", docx_bytes(TENDER)))]).json()["id"]
        data = client.get(f"/checks/{check_id}").json()
        assert data["status"] == "done", data["error"]
        assert len(data["requirements"]) == 6
        client.put(f"/checks/{check_id}/requirements", json=data["requirements"])
        bid = "УЧЕБНАЯ ЗАЯВКА DEMO-001\nRAM: 8 GB.\nStorage: 512 GB.\nWarranty: 12 months.\nДокументы: приложен только сертификат соответствия.\nDelivery: 14 days."
        client.put(f"/checks/{check_id}/bids/1", data={"name": "Beta"}, files=[("files", ("bid.docx", docx_bytes(bid)))])
        client.post(f"/checks/{check_id}/compare")
        data = client.get(f"/checks/{check_id}").json()
        assert data["status"] == "done", data["error"]
        assert data["results"]["1"]["score"]["percent"] == 32
        reqs = data["requirements"]
        reqs[0]["condition"] = "Оперативная память — не менее 8 ГБ."
        client.put(f"/checks/{check_id}/requirements", json=reqs)
        client.post(f"/checks/{check_id}/compare")
        assert client.get(f"/checks/{check_id}").json()["results"]["1"]["score"]["percent"] == 52


def test_demo_refuses_arbitrary_documents(tmp_path, monkeypatch):
    from app.main import create_app
    monkeypatch.setenv("AI_MODE", "demo")
    with TestClient(create_app(tmp_path)) as client:
        id = client.post("/checks", files=[("files", ("real.docx", docx_bytes("Arbitrary tender")))]).json()["id"]
        data = client.get(f"/checks/{id}").json()
        assert data["status"] == "failed"
        assert "учебн" in data["error"].lower()
