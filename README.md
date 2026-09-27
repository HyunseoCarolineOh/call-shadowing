# call-shadowing

Record an outbound sales call in the browser and get a Notion call log a few minutes later.
The audio goes to S3, Amazon Transcribe turns it into text, and Claude on Amazon Bedrock writes the hashtags, the call log, and a next action.
Everything lands in one Notion database row, with key points and the full transcript appended to the page body.
A CSV/XLSX sheet of target accounts can be imported as to-do rows, and team meetings can be recorded as meeting notes.
Tagging rules live in a plain markdown file (`rules/local.md`), so each team can plug in its own scheme without touching code.
Stack: FastAPI, vanilla JS, boto3, Notion REST API. Single user, runs on localhost. MIT.

## 무엇을 하는 도구인가

아웃바운드 콜(회사가 먼저 고객에게 전화를 거는 영업 콜)을 녹음하면 콜로그가 자동으로 남는다.
브라우저에서 녹음 버튼을 누르고 통화한다. 정지하면 파이프라인이 돈다.
몇 분 뒤 노션 데이터베이스 행에 해시태그, 콜로그, 날짜, 상태가 채워진다.
페이지 본문에는 핵심 포인트와 전사 전문이 붙는다.

- 투두 관리: 노션 DB 행이 곧 투두다. 상태 탭(Todo, Nextaction, Nopickup, Done)으로 본다.
- 시트 가져오기: 대상 계정 목록(CSV/XLSX)을 올리면 투두 행을 만든다. 중복 계정은 건너뛴다.
- 회의 녹음: 녹음 유형을 "팀 회의"로 바꾸면 해시태그 없이 회의록(요약, 논의, 결정, 액션 아이템)을 쓴다.

## 동작 구조

```
브라우저 (MediaRecorder, webm)
  → FastAPI (app.py)                 업로드 받고 백그라운드로 파이프라인 실행
  → Amazon S3                        녹음 원본 저장 (raw/YYYY-MM-DD/계정_id.webm)
  → Amazon Transcribe                전사, 화자 2명 분리
  → Amazon Bedrock (Claude)          rules/*.md 규칙으로 해시태그·콜로그·다음 액션을 JSON으로 생성
  → Notion                           행 속성 갱신 + 페이지 본문에 콜 노트·전사 전문 추가
```

진행 상황은 UI 우하단에 단계별로 표시된다. 실패하면 원인 메시지가 그 자리에 뜬다.

## 준비물

- Python 3.11 이상
- AWS 계정. S3, Transcribe, Bedrock(Claude 모델 접근 허용) 권한이 있는 자격증명
- 노션 워크스페이스와 통합(integration) 토큰
- 크롬 계열 브라우저 (MediaRecorder의 webm 녹음을 쓴다)

## 설치

```bash
git clone <this repo> call-shadowing
cd call-shadowing
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash. macOS/Linux는 source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### AWS 자격증명

boto3가 읽는 어떤 방식이든 된다. `aws configure`, `aws sso login`, 환경변수, 프로파일 모두 가능하다.
리전은 `.env`의 `AWS_REGION`(기본 `ap-northeast-2`)을 쓴다. Bedrock에서 쓰려는 모델은 그 리전에서 접근 허용을 받아 둔다.
자격증명이 만료되면 UI에 안내 문구가 뜬다. 문구는 `AWS_CREDS_HINT`로 바꿀 수 있다.

### 노션 설정

1. https://www.notion.so/my-integrations 에서 통합을 만든다. 권한은 콘텐츠 읽기, 업데이트, 삽입.
2. 콜로그를 기록할 데이터베이스를 만든다. 아래 속성이 이 이름 그대로 있어야 한다.
3. 그 데이터베이스 페이지에서 우상단 메뉴 → 연결 → 방금 만든 통합을 추가한다.
4. 데이터베이스 URL의 32자리 hex를 `.env`의 `NOTION_DATABASE_ID`에, 통합 토큰을 `NOTION_TOKEN`에 넣는다.

코드가 기대하는 속성(`notion_client.py` 기준):

| 속성 이름 | 유형 | 용도 |
|---|---|---|
| `Account` | 제목(title) | 계정명. 시트 가져오기의 중복 판정 기준 |
| `상태` | 상태(status) | 옵션 `시작 전`, `Nopickup`, `진행 중`, `완료`. 이름을 바꾸면 `config.py`의 `STATUS_MAP`도 맞춘다 |
| `Caller` | 선택(select) | 콜한 사람. UI 상단 셀렉터 값이 들어간다 |
| `Campaign` | 선택(select) | 캠페인명. 시트 가져오기나 직접 추가 때 입력 |
| `Date` | 날짜(date) | 콜 완료일. 자동 기입 |
| `Call Log` | 텍스트(rich text) | AI가 쓴 콜로그. 2000자 단위로 쪼개 저장 |

상태 속성 이름이 `상태`인 것은 이 도구가 한국어 워크스페이스에서 시작했기 때문이다. 다른 이름을 쓰려면 `notion_client.py`의 `"상태"`를 바꾼다.

### 점검

```bash
python setup.py
```

S3 버킷이 없으면 만들고(퍼블릭 접근 차단), 노션 DB 연결을 확인한다. 실패 항목은 메시지로 알려 준다.

## 환경변수

`.env.example`을 복사해 `.env`를 만든다. `.env`는 git에 올라가지 않는다.

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `NOTION_TOKEN` | 예 | 없음 | 노션 통합 토큰 |
| `NOTION_DATABASE_ID` | 예 | 없음 | 콜로그 DB ID (32자리 hex). 비어 있으면 노션 호출 때 안내 메시지와 함께 실패 |
| `S3_BUCKET` | 예 | 없음 | 녹음 저장 버킷. 전 세계에서 고유해야 하므로 계정 ID 등을 붙인다 |
| `AWS_REGION` | 아니오 | `ap-northeast-2` | S3, Transcribe, Bedrock 리전 |
| `BEDROCK_MODEL_ID` | 아니오 | `global.anthropic.claude-sonnet-4-6` | Bedrock 모델 ID |
| `TRANSCRIBE_LANGUAGE` | 아니오 | `ko-KR` | Transcribe 언어 코드 |
| `CALLER_OPTIONS` | 아니오 | `Me` | Caller 셀렉터 목록. 쉼표 구분 |
| `DEFAULT_CALLER` | 아니오 | 목록 첫 항목 | 기본 선택 Caller. 시트 가져오기의 `Owner` 필터 값으로도 쓴다 |
| `AWS_CREDS_HINT` | 아니오 | 영문 안내 문구 | 자격증명 만료 시 UI에 보여줄 문구. 회사별 갱신 명령을 넣어 두면 편하다 |

## 실행

```bash
python -m uvicorn app:app --port 8787
```

브라우저에서 http://localhost:8787 을 연다. 마이크 권한을 허용한다.

## 사용법

- 시트 가져오기: CSV 또는 XLSX를 올리고 캠페인명을 넣는다. 계정명 열은 `참가업체명`, `Account`, `업체명`, `회사명` 중 첫 번째로 찾는 열을 쓴다. "내 행만" 체크가 켜져 있으면 `Owner` 열이 `DEFAULT_CALLER`와 같은 행만 만든다.
- 직접 추가: 계정명과 캠페인(선택)을 넣고 추가한다. 회의 녹음은 여기서 회의 제목으로 행을 만들고 그 행에서 녹음한다.
- 녹음: 행의 녹음 버튼을 누르고 통화한다. 정지하면 업로드되고 우하단에 진행 상황이 뜬다.
- Nopickup: 연결이 안 됐으면 이 버튼을 누른다. 상태와 날짜만 기입한다.
- 상태 이동: AI가 미연결로 판정하면 Nopickup, 다음 액션이 있으면 Nextaction, 없으면 Done.

## 태그 규칙 바꾸기

해시태그 정의와 콜로그 형식은 `rules/` 폴더의 마크다운 한 장이다. 이 파일 내용이 그대로 시스템 프롬프트에 들어간다.

- `rules/example.md`: 공개용 샘플. 태그 8개와 B·W·A·N·T 형식의 로그 틀이 들어 있다.
- `rules/local.md`: 내 팀 규칙. 이 파일이 있으면 example.md 대신 쓴다. git에 올라가지 않는다.

시작은 `cp rules/example.md rules/local.md`. 태그 이름, 판정 기준, 로그 항목을 팀 기준으로 고친다.
AI가 돌려주는 JSON의 키(`nopickup`, `hashtags`, `call_log`, `next_action`, `key_points`)는 `prompts.py`가 고정한다. 규칙 파일에서는 건드리지 않아도 된다.

## 상태 이름 매핑

노션 상태 옵션은 API로 추가할 수 없다. 그래서 도구 안 상태 이름을 기존 옵션에 매핑한다 (`config.py`의 `STATUS_MAP`).

| 도구 | 노션 |
|---|---|
| Todo | 시작 전 |
| Nopickup | Nopickup |
| Nextaction | 진행 중 |
| Done | 완료 |

노션에서 옵션 이름을 바꾸면 오른쪽 값을 같이 바꾼다.

## 프로젝트 구조

```
app.py             FastAPI 서버, 백그라운드 파이프라인, 진행 상황 폴링 API
static/index.html  UI (투두 목록, 녹음, 시트 업로드). 프레임워크 없음
aws_pipeline.py    S3 업로드 → Transcribe → Bedrock Claude. 버킷 생성 함수 포함
notion_client.py   노션 REST API (조회, 생성, 속성 갱신, 본문 append)
prompts.py         시스템 프롬프트. rules/local.md 또는 rules/example.md를 읽어 조립
importer.py        CSV/XLSX → 노션 투두 행 (중복 계정 건너뜀)
config.py          .env 로딩과 기본값
setup.py           1회 점검 (버킷 생성, 노션 연결)
rules/example.md   샘플 태그 규칙 (커밋됨)
rules/local.md     내 팀 규칙 (git 무시)
```

## 한계

- 로컬 1인용이다. 인증이 없으니 외부에 노출하지 않는다.
- 진행 상태는 프로세스 메모리에만 있다. 서버를 재시작하면 진행 표시가 사라진다. 노션에 쓴 결과는 남는다.
- 녹음은 브라우저 마이크 입력만 잡는다. 상대 음성은 스피커폰이나 헤드셋 루프백으로 들어와야 한다.
- 전사 언어는 한 번에 하나다(`TRANSCRIBE_LANGUAGE`). 프롬프트와 샘플 규칙은 한국어 기준이다.
- Transcribe 대기는 최대 15분이다. 넘기면 실패로 표시한다.
- 콜 페이지 하나에 녹음을 여러 번 하면 노트가 아래로 계속 붙는다. 정리는 노션에서 직접 한다.

## 라이선스

MIT. `LICENSE` 참고.
