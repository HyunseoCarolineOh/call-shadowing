"""콜 섀도잉 툴 — FastAPI 서버.

실행: uvicorn app:app --port 8787
UI:  http://localhost:8787
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import aws_pipeline
import config
import importer
import notion_client

app = FastAPI(title="call-shadowing")

# 진행 중 파이프라인 상태 (UI가 폴링)
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        JOBS.setdefault(job_id, {})
        JOBS[job_id].update(kwargs, updated=time.time())


# ---------- UI ----------

@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# ---------- API ----------

@app.get("/api/meta")
def meta():
    return {
        "callers": config.CALLER_OPTIONS,
        "default_caller": config.DEFAULT_CALLER,
        "statuses": list(config.STATUS_MAP.keys()),
    }


@app.get("/api/calls")
def list_calls():
    try:
        return notion_client.query_calls()
    except notion_client.NotionError as e:
        raise HTTPException(500, str(e))


@app.post("/api/calls")
def create_call(account: str = Form(...), campaign: str = Form("")):
    if not account.strip():
        raise HTTPException(400, "계정명을 입력해주세요.")
    try:
        return notion_client.create_call(account.strip(), campaign=campaign.strip())
    except notion_client.NotionError as e:
        raise HTTPException(500, str(e))


@app.post("/api/calls/{page_id}/nopickup")
def nopickup(page_id: str, caller: str = Form(config.DEFAULT_CALLER)):
    """전화 미연결 — 상태 Nopickup + 오늘 날짜 + Caller 기입."""
    try:
        notion_client.update_call(page_id, status="Nopickup", caller=caller, date_today=True)
        return {"ok": True}
    except notion_client.NotionError as e:
        raise HTTPException(500, str(e))


@app.post("/api/calls/{page_id}/audio")
async def upload_audio(
    page_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    account: str = Form("call"),
    caller: str = Form(config.DEFAULT_CALLER),
    kind: str = Form("call"),  # "call"(고객 콜) | "meeting"(팀 회의)
):
    """녹음 업로드 → 백그라운드로 S3→Transcribe→Bedrock→노션 파이프라인."""
    data = await file.read()
    if len(data) < 1000:
        raise HTTPException(400, "녹음이 너무 짧아요.")
    ext = "webm" if "webm" in (file.content_type or "") else (file.filename or "a.webm").rsplit(".", 1)[-1]
    job_id = uuid.uuid4().hex[:10]
    _set_job(job_id, account=account, page_id=page_id, stage="대기 중", error=None, done=False)
    background.add_task(_run_pipeline, job_id, page_id, account, caller, data, ext, kind)
    return {"job_id": job_id}


@app.get("/api/jobs")
def jobs():
    with _jobs_lock:
        return JOBS


@app.post("/api/import")
async def import_sheet(
    file: UploadFile = File(...),
    campaign: str = Form(...),
    only_mine: bool = Form(True),
):
    data = await file.read()
    try:
        result = importer.import_sheet(
            file.filename or "sheet.csv",
            data,
            campaign=campaign.strip(),
            only_owner=config.DEFAULT_CALLER if only_mine else None,
        )
    except (ValueError, notion_client.NotionError) as e:
        raise HTTPException(400, str(e))
    return result


# ---------- 파이프라인 ----------

def _run_pipeline(job_id: str, page_id: str, account: str, caller: str, data: bytes, ext: str, kind: str = "call") -> None:
    try:
        _set_job(job_id, stage="S3 업로드 중")
        s3_uri = aws_pipeline.upload_audio(data, account, ext=ext)

        _set_job(job_id, stage="전사 중 (Transcribe)")
        transcript = aws_pipeline.transcribe(s3_uri, media_format=ext)
        if not transcript.strip():
            raise aws_pipeline.PipelineError("전사 결과가 비었어요 (무음 녹음?)")

        context = notion_client.get_page_context(page_id)

        if kind == "meeting":
            _set_job(job_id, stage="AI 회의록 작성 중 (Bedrock)")
            result = aws_pipeline.generate_meeting_note(account, transcript, context)
            _set_job(job_id, stage="노션 기입 중")
            notion_client.update_call(
                page_id,
                status="Done",
                caller=caller,
                call_log=f"📝 회의록: {result['summary']}",
                date_today=True,
            )
            notion_client.append_meeting_note(
                page_id,
                summary=result["summary"],
                key_points=result.get("key_points", []),
                decisions=result.get("decisions", []),
                action_items=result.get("action_items", []),
                transcript=transcript,
                s3_uri=s3_uri,
            )
            _set_job(job_id, stage="완료 → 회의록 작성됨", done=True)
            return

        _set_job(job_id, stage="AI 콜로그 작성 중 (Bedrock)")
        result = aws_pipeline.generate_call_log(account, transcript, context)

        _set_job(job_id, stage="노션 기입 중")
        if result["nopickup"]:
            status = "Nopickup"
        elif result.get("next_action", "").strip():
            status = "Nextaction"
        else:
            status = "Done"
        notion_client.update_call(
            page_id,
            status=status,
            caller=caller,
            call_log=result["call_log"],
            date_today=True,
        )
        notion_client.append_call_note(
            page_id,
            key_points=result.get("key_points", []),
            next_action=result.get("next_action", ""),
            transcript=transcript,
            s3_uri=s3_uri,
        )
        _set_job(job_id, stage=f"완료 → {status}", done=True)
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 UI에 보여준다
        _set_job(job_id, stage="실패", error=str(e), done=True)


@app.exception_handler(Exception)
def unhandled(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
