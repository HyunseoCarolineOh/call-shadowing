"""노션 REST API 래퍼. 통합 토큰(.env NOTION_TOKEN)으로 콜로그 DB를 읽고 쓴다."""
from __future__ import annotations

import datetime

import requests

import config

BASE = "https://api.notion.com/v1"


class NotionError(RuntimeError):
    pass


def _headers() -> dict:
    if not config.NOTION_TOKEN:
        raise NotionError(
            "NOTION_TOKEN이 없습니다. .env에 노션 통합 토큰을 넣어주세요 (README 참고)."
        )
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _database_id() -> str:
    if not config.NOTION_DATABASE_ID:
        raise NotionError(
            "NOTION_DATABASE_ID가 없습니다. .env에 콜로그를 기록할 노션 데이터베이스 ID를 넣어주세요 (README 참고)."
        )
    return config.NOTION_DATABASE_ID


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    resp = requests.request(method, f"{BASE}{path}", headers=_headers(), json=payload, timeout=30)
    if resp.status_code >= 400:
        raise NotionError(f"Notion API {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _plain(rich: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich)


def _rich(text: str) -> list:
    # rich_text 항목당 2000자 제한 → 쪼갠다
    return [{"type": "text", "text": {"content": text[i : i + 2000]}} for i in range(0, len(text), 2000)] or [
        {"type": "text", "text": {"content": ""}}
    ]


def _simplify(page: dict) -> dict:
    props = page["properties"]

    def sel(name):
        v = props.get(name, {}).get("select")
        return v["name"] if v else ""

    status_raw = (props.get("상태", {}).get("status") or {}).get("name", "")
    date_raw = props.get("Date", {}).get("date") or {}
    return {
        "id": page["id"],
        "url": page["url"],
        "account": _plain(props.get("Account", {}).get("title", [])),
        "caller": sel("Caller"),
        "campaign": sel("Campaign"),
        "date": date_raw.get("start", ""),
        "status_notion": status_raw,
        "status": config.STATUS_MAP_REVERSE.get(status_raw, status_raw),
        "call_log": _plain(props.get("Call Log", {}).get("rich_text", [])),
    }


def query_calls() -> list[dict]:
    """DB 전체 행을 최신 생성순으로 반환."""
    rows, cursor = [], None
    while True:
        payload: dict = {"page_size": 100, "sorts": [{"timestamp": "created_time", "direction": "descending"}]}
        if cursor:
            payload["start_cursor"] = cursor
        data = _request("POST", f"/databases/{_database_id()}/query", payload)
        rows += [_simplify(p) for p in data["results"]]
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


def create_call(account: str, campaign: str = "", body_lines: list[str] | None = None) -> dict:
    """새 콜 행을 Todo 상태로 생성. body_lines는 본문 불릿(부스 정보 등)."""
    props: dict = {
        "Account": {"title": _rich(account)},
        "상태": {"status": {"name": config.STATUS_MAP["Todo"]}},
    }
    if campaign:
        props["Campaign"] = {"select": {"name": campaign}}
    children = []
    if body_lines:
        children = [
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rich(line)},
            }
            for line in body_lines
            if line.strip()
        ]
    payload = {
        "parent": {"database_id": _database_id()},
        "properties": props,
        "children": children[:100],
    }
    page = _request("POST", "/pages", payload)
    return _simplify(page)


def update_call(
    page_id: str,
    status: str | None = None,  # 툴 상태 이름 (Todo/Nopickup/Nextaction/Done)
    caller: str | None = None,
    call_log: str | None = None,
    date_today: bool = False,
) -> None:
    props: dict = {}
    if status:
        props["상태"] = {"status": {"name": config.STATUS_MAP[status]}}
    if caller:
        props["Caller"] = {"select": {"name": caller}}
    if call_log is not None:
        props["Call Log"] = {"rich_text": _rich(call_log)}
    if date_today:
        today = datetime.date.today().isoformat()
        props["Date"] = {"date": {"start": today}}
    if props:
        _request("PATCH", f"/pages/{page_id}", {"properties": props})


def get_page_context(page_id: str, max_chars: int = 3000) -> str:
    """페이지 본문을 평문으로 — LLM 사전 정보용."""
    texts, cursor = [], None
    while True:
        path = f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = _request("GET", path)
        for block in data["results"]:
            inner = block.get(block["type"], {})
            if isinstance(inner, dict) and "rich_text" in inner:
                line = _plain(inner["rich_text"])
                if line.strip():
                    texts.append(line)
        if not data.get("has_more") or sum(len(t) for t in texts) > max_chars:
            break
        cursor = data["next_cursor"]
    return "\n".join(texts)[:max_chars]


def append_call_note(page_id: str, key_points: list[str], next_action: str, transcript: str, s3_uri: str) -> None:
    """페이지 본문 끝에 '전사 기반 콜 노트' 섹션 + 전사 전문 토글을 붙인다."""
    today = datetime.date.today().isoformat()

    def bullet(text):
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich(text)},
        }

    transcript_paras = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(chunk)}}
        for chunk in [transcript[i : i + 1900] for i in range(0, min(len(transcript), 90 * 1900), 1900)]
    ]
    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich(f"전사 기반 콜 노트 ({today}, 자동 생성)")},
        },
        *[bullet(p) for p in key_points if p.strip()],
    ]
    if next_action.strip():
        children.append(bullet(f"Next: {next_action.strip()}"))
    children.append(bullet(f"녹음 파일: {s3_uri}"))
    children.append(
        {
            "object": "block",
            "type": "toggle",
            "toggle": {"rich_text": _rich("전사 전문"), "children": transcript_paras[:99]},
        }
    )
    _request("PATCH", f"/blocks/{page_id}/children", {"children": children})


def append_meeting_note(
    page_id: str,
    summary: str,
    key_points: list[str],
    decisions: list[str],
    action_items: list[str],
    transcript: str,
    s3_uri: str,
) -> None:
    """팀 회의 녹음 → 페이지 본문에 회의록 섹션 + 전사 전문 토글."""
    today = datetime.date.today().isoformat()

    def bullet(text):
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich(text)},
        }

    def todo(text):
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": _rich(text), "checked": False}}

    def heading3(text):
        return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rich(text)}}

    transcript_paras = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(chunk)}}
        for chunk in [transcript[i : i + 1900] for i in range(0, min(len(transcript), 90 * 1900), 1900)]
    ]
    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich(f"회의록 ({today}, 자동 생성)")},
        },
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(f"요약: {summary}")}},
    ]
    if key_points:
        children.append(heading3("논의 내용"))
        children += [bullet(p) for p in key_points if p.strip()]
    if decisions:
        children.append(heading3("결정 사항"))
        children += [bullet(d) for d in decisions if d.strip()]
    if action_items:
        children.append(heading3("액션 아이템"))
        children += [todo(a) for a in action_items if a.strip()]
    children.append(bullet(f"녹음 파일: {s3_uri}"))
    children.append(
        {
            "object": "block",
            "type": "toggle",
            "toggle": {"rich_text": _rich("전사 전문"), "children": transcript_paras[:99]},
        }
    )
    _request("PATCH", f"/blocks/{page_id}/children", {"children": children[:100]})


def check_connection() -> str:
    """토큰·DB 연결 확인. 성공 시 DB 제목 반환."""
    data = _request("GET", f"/databases/{_database_id()}")
    return _plain(data.get("title", []))
