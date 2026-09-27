# -*- coding: utf-8 -*-
"""1회 셋업 점검: S3 버킷 생성(없으면) + 노션 DB 연결 확인.  실행: python setup.py

.env를 채운 뒤 한 번 돌려서 자격증명·버킷·노션 연결이 모두 되는지 본다.
"""
import sys

import aws_pipeline
import config
import notion_client


def main() -> int:
    ok = True
    print(f"AWS 리전: {config.AWS_REGION} / Bedrock 모델: {config.BEDROCK_MODEL_ID}")

    try:
        created = aws_pipeline.ensure_bucket()
        print(("버킷 생성 완료: " if created else "버킷 이미 있음: ") + config.S3_BUCKET)
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 메시지를 그대로 보여준다
        ok = False
        print("[S3] 실패:", e)

    try:
        title = notion_client.check_connection()
        print("노션 DB 연결 확인:", title or "(제목 없음)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[Notion] 실패:", e)

    print("점검 완료" if ok else "일부 실패. 위 메시지를 보고 .env를 고쳐주세요 (README 참고).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
