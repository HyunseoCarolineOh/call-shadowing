"""AWS 구간: S3 업로드 → Transcribe(ko-KR) → Bedrock Claude로 콜로그 생성."""
from __future__ import annotations

import datetime
import json
import re
import time
import uuid

import boto3
import requests
from botocore.exceptions import ClientError

import config
import prompts


class PipelineError(RuntimeError):
    pass


def _check_creds(err: ClientError) -> None:
    code = err.response.get("Error", {}).get("Code", "")
    if code in ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"):
        # 안내 문구는 config.AWS_CREDS_HINT (.env의 AWS_CREDS_HINT로 회사별 갱신 명령을 넣을 수 있다)
        raise PipelineError(config.AWS_CREDS_HINT) from err
    raise err


def _bucket() -> str:
    """S3_BUCKET 설정 확인. 비어 있거나 자리표시자면 안내 메시지와 함께 실패."""
    if not config.S3_BUCKET or "<" in config.S3_BUCKET:
        raise PipelineError(
            "S3_BUCKET이 설정되지 않았어요. .env에 S3_BUCKET=<전 세계에서 고유한 버킷 이름>을 넣고 "
            "python setup.py로 버킷을 만들어주세요 (README 참고)."
        )
    return config.S3_BUCKET


def ensure_bucket() -> bool:
    """녹음 저장용 버킷이 없으면 만든다 (퍼블릭 접근 차단). 새로 만들었으면 True, 이미 있으면 False."""
    bucket = _bucket()
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    try:
        s3.head_bucket(Bucket=bucket)
        return False
    except ClientError as e:
        if e.response.get("Error", {}).get("Code", "") not in ("404", "NoSuchBucket", "NotFound"):
            _check_creds(e)
    params: dict = {"Bucket": bucket}
    if config.AWS_REGION != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": config.AWS_REGION}
    s3.create_bucket(**params)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return True


def upload_audio(data: bytes, account: str, ext: str = "webm") -> str:
    """녹음 바이트를 S3에 저장하고 s3:// URI 반환."""
    bucket = _bucket()
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", account)[:40] or "call"
    key = f"raw/{datetime.date.today().isoformat()}/{safe}_{uuid.uuid4().hex[:8]}.{ext}"
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=f"audio/{ext}")
    except ClientError as e:
        _check_creds(e)
    return f"s3://{bucket}/{key}"


def transcribe(s3_uri: str, media_format: str = "webm") -> str:
    """Transcribe 잡을 돌리고 전사 텍스트를 반환 (완료까지 폴링)."""
    client = boto3.client("transcribe", region_name=config.AWS_REGION)
    job_name = f"call-shadowing-{uuid.uuid4().hex[:12]}"
    try:
        client.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode=config.TRANSCRIBE_LANGUAGE,
            MediaFormat=media_format,
            Media={"MediaFileUri": s3_uri},
            Settings={"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2},
        )
    except ClientError as e:
        _check_creds(e)

    deadline = time.time() + config.TRANSCRIBE_TIMEOUT_SECONDS
    while time.time() < deadline:
        job = client.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]
        if status == "COMPLETED":
            url = job["Transcript"]["TranscriptFileUri"]
            return _parse_transcript(requests.get(url, timeout=30).json())
        if status == "FAILED":
            raise PipelineError(f"Transcribe 실패: {job.get('FailureReason', '')}")
        time.sleep(config.TRANSCRIBE_POLL_SECONDS)
    raise PipelineError("Transcribe 시간 초과 (15분)")


def _parse_transcript(payload: dict) -> str:
    """화자 구분이 있으면 '화자N: ...' 줄로, 없으면 통짜 텍스트로."""
    results = payload.get("results", {})
    segments = results.get("audio_segments") or []
    if segments:
        lines = []
        for seg in segments:
            speaker = seg.get("speaker_label", "").replace("spk_", "화자")
            text = seg.get("transcript", "").strip()
            if text:
                lines.append(f"{speaker}: {text}" if speaker else text)
        if lines:
            return "\n".join(lines)
    transcripts = results.get("transcripts") or []
    return (transcripts[0].get("transcript", "") if transcripts else "").strip()


def _converse_json(system_prompt: str, user_prompt: str, required_keys: tuple) -> dict:
    """Bedrock Claude 호출 후 JSON 파싱·필수 키 검증."""
    client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    try:
        resp = client.converse(
            modelId=config.BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0.2},
        )
    except ClientError as e:
        _check_creds(e)
    text = resp["output"]["message"]["content"][0]["text"].strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise PipelineError(f"AI 응답이 JSON이 아님: {text[:200]}") from e
    for key in required_keys:
        if key not in result:
            raise PipelineError(f"AI 응답에 '{key}' 누락: {text[:200]}")
    return result


def generate_call_log(account: str, transcript: str, page_context: str) -> dict:
    """Bedrock Claude로 해시태그·콜로그·Next Action 생성. dict 반환."""
    result = _converse_json(
        prompts.SYSTEM_PROMPT,
        prompts.build_user_prompt(account, transcript, page_context),
        required_keys=("nopickup", "hashtags", "call_log"),
    )
    result.setdefault("next_action", "")
    result.setdefault("key_points", [])
    return result


def generate_meeting_note(title: str, transcript: str, page_context: str) -> dict:
    """팀 회의 녹음 → 회의록(요약·논의·결정·액션아이템) 생성."""
    result = _converse_json(
        prompts.MEETING_SYSTEM_PROMPT,
        prompts.build_meeting_prompt(title, transcript, page_context),
        required_keys=("summary", "key_points"),
    )
    result.setdefault("decisions", [])
    result.setdefault("action_items", [])
    return result
