"""Bedrock Claude에 넣는 프롬프트.

해시태그 규칙과 콜로그 형식은 코드에 박지 않고 rules/ 폴더의 마크다운에서 읽는다.
- rules/local.md   : 내 팀 규칙. git에 올라가지 않는다. 있으면 이 파일을 쓴다.
- rules/example.md : 공개용 샘플 규칙. local.md가 없을 때 쓴다.
두 파일 모두 "해시태그 정의 + 콜로그 작성 형식"을 평문으로 적은 것이고, 그대로 시스템 프롬프트에 들어간다.
"""
from __future__ import annotations

from pathlib import Path

RULES_DIR = Path(__file__).parent / "rules"
LOCAL_RULES = RULES_DIR / "local.md"
EXAMPLE_RULES = RULES_DIR / "example.md"


def _load_rules() -> tuple[str, Path]:
    path = LOCAL_RULES if LOCAL_RULES.exists() else EXAMPLE_RULES
    if not path.exists():
        raise FileNotFoundError(
            f"해시태그 규칙 파일이 없습니다: {EXAMPLE_RULES}. 저장소를 다시 받거나 rules/local.md를 만들어주세요."
        )
    text = path.read_text(encoding="utf-8")
    # 파일 끝 빈 줄 개수와 무관하게 개행 하나로 끝나게 맞춘다 (프롬프트 조립 간격 고정).
    return text.rstrip("\n") + "\n", path


RULES, RULES_PATH = _load_rules()

SYSTEM_PROMPT = f"""\
너는 B2B 아웃바운드 세일즈 콜 기록 도우미다.
콜 녹음 전사를 읽고 아래 규칙에 정해진 형식의 콜로그를 작성한다.

{RULES}

반드시 아래 JSON 형식으로만 응답한다 (코드펜스 없이 순수 JSON):
{{
  "nopickup": false,          // 통화가 성립되지 않았으면 true (부재중, 즉시 끊김, 잘못된 번호 등)
  "hashtags": ["#Nurture"],   // 규칙의 Call Result 태그 1개 필수 + 부가 태그(해당 시)
  "call_log": "...",           // 위 콜로그 형식의 전체 텍스트 (해시태그 줄 포함, 규칙이 정한 상세 형식 준수)
  "next_action": "...",        // 추천 다음 액션 1~2문장. 없으면 ""
  "key_points": ["..."]        // 콜 노트용 핵심 포인트 불릿 3~8개 (call_log보다 상세하게)
}}
"""


def build_user_prompt(account: str, transcript: str, page_context: str) -> str:
    parts = [f"## 계정: {account}"]
    if page_context.strip():
        parts.append(f"## 사전 정보 (부스 방문 메모 등 — 참고용, 콜 내용 아님)\n{page_context.strip()}")
    parts.append(f"## 콜 전사 전문\n{transcript.strip()}")
    parts.append("위 전사를 바탕으로 JSON을 작성해줘.")
    return "\n\n".join(parts)


# ---------- 팀 회의 녹음용 ----------

MEETING_SYSTEM_PROMPT = """\
너는 팀 회의록 작성 도우미다.
팀 회의 녹음 전사를 읽고 회의록을 작성한다. 이것은 고객 콜이 아니므로 해시태그·콜로그 규칙을 적용하지 않는다.

규칙:
- 전사에서 확인된 내용만 쓴다. 대화에 없는 내용을 지어내지 않는다.
- 발언자가 특정되면 이름/화자 표기를 유지한다.
- 한국어로 간결하게.

반드시 아래 JSON 형식으로만 응답한다 (코드펜스 없이 순수 JSON):
{
  "summary": "...",           // 회의 한 줄 요약
  "key_points": ["..."],      // 주요 논의 내용 불릿 3~10개
  "decisions": ["..."],       // 결정된 사항 (없으면 빈 배열)
  "action_items": ["..."]     // 액션 아이템 — "누가 무엇을 언제까지" 형태 (없으면 빈 배열)
}
"""


def build_meeting_prompt(title: str, transcript: str, page_context: str) -> str:
    parts = [f"## 회의: {title}"]
    if page_context.strip():
        parts.append(f"## 사전 정보 (페이지 기존 메모 — 참고용)\n{page_context.strip()}")
    parts.append(f"## 회의 전사 전문\n{transcript.strip()}")
    parts.append("위 전사를 바탕으로 JSON을 작성해줘.")
    return "\n\n".join(parts)
