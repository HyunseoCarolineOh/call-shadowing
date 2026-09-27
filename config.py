"""콜 섀도잉 툴 설정. 값은 .env(또는 환경변수)로 덮어쓴다. 항목 설명은 README의 환경변수 표 참고."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _csv(name: str, default: list[str]) -> list[str]:
    """쉼표 구분 환경변수를 리스트로. 비어 있으면 default."""
    items = [s.strip() for s in os.getenv(name, "").split(",") if s.strip()]
    return items or list(default)


# --- Notion ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
# 콜로그를 기록할 노션 데이터베이스 ID (DB URL의 32자리 hex).
# 비어 있으면 노션을 호출하는 순간 안내 메시지와 함께 실패한다 (notion_client._database_id).
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
NOTION_VERSION = "2022-06-28"

# --- AWS ---
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
# 녹음 파일을 저장할 S3 버킷. 버킷 이름은 전 세계에서 고유해야 하므로 계정 ID 등을 붙여 직접 정한다.
# 비어 있으면 업로드 시점에 안내 메시지와 함께 실패한다 (aws_pipeline._bucket).
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
TRANSCRIBE_LANGUAGE = os.getenv("TRANSCRIBE_LANGUAGE", "ko-KR")
TRANSCRIBE_POLL_SECONDS = 5
TRANSCRIBE_TIMEOUT_SECONDS = 15 * 60
# 자격증명 만료 시 UI에 보여줄 안내 문구. 회사 환경에 맞는 갱신 명령이 있으면 .env에서 덮어쓴다.
AWS_CREDS_HINT = os.getenv(
    "AWS_CREDS_HINT",
    "AWS credentials expired. Refresh your credentials (e.g. `aws sso login`) and retry.",
)

# --- 앱 동작 ---
# Caller 셀렉터에 보일 이름들 (쉼표 구분). 섀도잉이라면 팀원 이름을 나열한다.
CALLER_OPTIONS = _csv("CALLER_OPTIONS", ["Me"])
# 기본 선택 Caller. 비어 있으면 CALLER_OPTIONS의 첫 항목. 목록에 없으면 맨 앞에 추가한다.
DEFAULT_CALLER = os.getenv("DEFAULT_CALLER", "").strip() or CALLER_OPTIONS[0]
if DEFAULT_CALLER not in CALLER_OPTIONS:
    CALLER_OPTIONS.insert(0, DEFAULT_CALLER)

# 툴에서 쓰는 상태 이름 → 노션 DB의 실제 상태 옵션 이름.
# 노션 상태 옵션은 API로 추가할 수 없어서 기존 옵션에 매핑한다.
# 노션 UI에서 옵션 이름을 바꾸면 여기 오른쪽 값만 같이 바꿔주면 된다.
STATUS_MAP = {
    "Todo": "시작 전",
    "Nopickup": "Nopickup",
    "Nextaction": "진행 중",
    "Done": "완료",
}
STATUS_MAP_REVERSE = {v: k for k, v in STATUS_MAP.items()}
