import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app.vault import report, store
from app.vault.schemas import EvidenceIn

def _png() -> str:
    import io

    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (40, 80), (240, 240, 250)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


PNG = _png()


@pytest.fixture(autouse=True)
def tmp_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO_MODE", True)


def capture(n=3, screenshot=True, **kw):
    return EvidenceIn(
        platform="Instagram",
        conversation="rahul_k_07",
        messages=[
            {"ref": f"m{i}", "sender": "rahul", "text": f"threat {i}", "time": f"21:0{i}", "flagged": i == 2}
            for i in range(1, n + 1)
        ],
        screenshots=[{"kind": "chat_capture", "data_url": f"data:image/png;base64,{PNG}"}] if screenshot else [],
        analysis={"category": "sextortion_blackmail", "risk_score": 85, "severity": "high", "reason": "x"},
        client_captured_at="1999-01-01T00:00:00Z",
        **kw,
    )


def statuses():
    return [r["status"] for r in store.verify()["records"]]


def raw_sql(sql, *args):
    conn = sqlite3.connect(config.DATA_DIR / "kavach.db")
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def test_saved_records_form_a_valid_chain():
    store.save_capture(capture())
    store.save_capture(capture(n=2, screenshot=False))
    rows = store.all_rows()
    assert len(rows) == 6  # 1 screenshot + 3 messages, then 2 messages
    assert rows[0]["prev_hash"] == store.GENESIS
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(rows, rows[1:]))
    result = store.verify()
    assert result["ok"] and result["head_hash"] == rows[-1]["hash"]


def test_server_timestamp_is_used_not_the_device_clock():
    store.save_capture(capture())
    row = store.all_rows()[0]
    assert row["client_captured_at"] == "1999-01-01T00:00:00Z"
    assert row["server_timestamp"].startswith("20") and not row["server_timestamp"].startswith("1999")


def test_editing_a_message_in_the_database_is_detected():
    store.save_capture(capture())
    raw_sql("UPDATE evidence SET message_text='ok sorry' WHERE id=3")
    result = store.verify()
    assert not result["ok"] and result["first_broken_id"] == 3
    assert statuses() == ["ok", "ok", "tampered", "after_break"]


def test_editing_the_timestamp_is_detected():
    store.save_capture(capture())
    raw_sql("UPDATE evidence SET server_timestamp='2020-01-01T00:00:00+00:00' WHERE id=2")
    assert store.verify()["first_broken_id"] == 2


def test_rehashed_edit_breaks_the_next_link():
    store.save_capture(capture())
    out = store.demo_tamper(record_id=3, rehash=True)
    assert out["before"] == "threat 2"
    assert statuses() == ["ok", "ok", "rehashed", "broken_link"]
    assert store.verify()["first_broken_id"] == 3


def test_deleting_a_record_is_detected():
    store.save_capture(capture())
    raw_sql("DELETE FROM evidence WHERE id=2")
    assert statuses()[1] == "broken_link"


def test_swapped_screenshot_file_is_detected():
    store.save_capture(capture())
    digest = store.all_rows()[0]["screenshot_sha256"]
    (config.DATA_DIR / "screenshots" / f"{digest}.png").write_bytes(b"not the original image")
    assert statuses()[0] == "file_changed"


def test_demo_tamper_defaults_to_a_flagged_message():
    store.save_capture(capture())
    out = store.demo_tamper(record_id=None, rehash=False)
    assert out["record_id"] == 3  # the flagged one


def test_report_renders_for_verified_and_broken_vaults():
    assert report.build_report().startswith(b"%PDF")  # empty vault
    store.save_capture(capture())
    ok_pdf = report.build_report()
    store.demo_tamper(record_id=None, rehash=False)
    broken_pdf = report.build_report()
    assert ok_pdf.startswith(b"%PDF") and broken_pdf.startswith(b"%PDF") and ok_pdf != broken_pdf
    # A replaced, unreadable screenshot must not stop the report from being produced.
    digest = store.all_rows()[0]["screenshot_sha256"]
    (config.DATA_DIR / "screenshots" / f"{digest}.png").write_bytes(b"garbage")
    assert report.build_report().startswith(b"%PDF")


def test_api_rejects_non_image_screenshots_and_gates_demo_endpoints(monkeypatch):
    from app.main import app

    client = TestClient(app)
    body = capture().model_dump()
    body["screenshots"][0]["data_url"] = "data:text/html;base64,PGgxPmhpPC9oMT4="
    assert client.post("/evidence", json=body).status_code == 400
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert client.post("/evidence/demo/reset").status_code == 403


# ---------- incident date + platform ----------

def test_incident_date_is_sealed_and_tampering_with_it_is_detected():
    store.save_capture(capture(incident_date="2026-10-14"))
    assert store.all_rows()[0]["incident_date"] == "2026-10-14"
    assert store.verify()["ok"]
    raw_sql("UPDATE evidence SET incident_date='2026-09-01' WHERE id=2")
    assert store.verify()["first_broken_id"] == 2


def test_records_without_incident_date_still_verify():
    store.save_capture(capture())  # e.g. sealed before the field existed
    assert store.all_rows()[0]["incident_date"] is None and store.verify()["ok"]


def test_old_vault_without_the_column_is_migrated(tmp_path):
    old_schema = store._SCHEMA.replace("    incident_date      TEXT,                   -- YYYY-MM-DD, stated by the complainant\n", "")
    assert "incident_date" not in old_schema
    db = sqlite3.connect(config.DATA_DIR / "kavach.db")
    db.executescript(old_schema)
    db.close()
    store.save_capture(capture(incident_date="2026-10-14"))
    assert store.verify()["ok"]


def test_future_dates_and_blank_platform_are_rejected():
    from datetime import date, timedelta

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        capture(incident_date=(date.today() + timedelta(days=5)).isoformat())
    with pytest.raises(ValidationError):
        EvidenceIn(platform="  ", conversation="x", messages=[{"ref": "1", "sender": "a", "text": "b"}])


def test_she_can_clear_the_vault_without_demo_mode(monkeypatch):
    from app.main import app

    client = TestClient(app)
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert client.post("/evidence", json=capture().model_dump()).status_code == 200
    assert client.get("/evidence").json()["records"]
    assert client.delete("/evidence").status_code == 200
    assert client.get("/evidence").json()["records"] == []
    assert not list((config.DATA_DIR / "screenshots").glob("*"))
