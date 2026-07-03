# YouTube Shorts Manager — 구현 명세서

> 이 문서는 Claude Code에서 `shorts_manager.py` 와 `README.md` 를 구현하기 위한 상세 명세입니다.
> 기존에 동작 검증된 로직을 최대한 재사용하고, 하나의 CLI 스크립트로 통합합니다.

---

## 1. 파일 구조

```
project/
├── shorts_manager.py   # 메인 CLI 스크립트 (단일 파일로 완결)
├── credentials.json    # Google OAuth 클라이언트 (사용자가 직접 발급, .gitignore)
├── token.json          # OAuth 토큰 자동 생성됨 (.gitignore)
├── README.md           # 사용 설명서
└── requirements.txt
```

---

## 2. requirements.txt

```
google-api-python-client>=2.0.0
google-auth-httplib2>=0.1.0
google-auth-oauthlib>=0.5.0
requests>=2.28.0
yt-dlp>=2024.0.0
```

> ffmpeg는 시스템에 별도 설치 필요 (pip 설치 불가)

---

## 3. CLI 인터페이스

### 3-1. 다운로드 + 시트 생성/업데이트

```bash
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --popular 1 100 \          # 인기순 1~100번째
  --drive-folder "쇼츠백업" \
  --sheet-name "쇼츠현황" \
  --youtube-api-key "AIza..."
```

### 3-2. 범위 옵션 (셋 중 하나만 사용)

| 옵션 | 설명 | 예시 |
|------|------|------|
| `--popular START END` | 인기순(조회수 기준) START~END번째 | `--popular 1 100` |
| `--latest START END` | 최신순(업로드일 기준) START~END번째 | `--latest 1 50` |
| `--all` | 전체 다운로드 | `--all` |

START, END는 1-based index. END는 포함.

### 3-3. 시트만 업데이트 (다운로드 없이 수치만 갱신)

```bash
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --update-sheet "쇼츠현황" \
  --youtube-api-key "AIza..."
```

### 3-4. 전체 인수 목록

```
--channel           채널 Shorts 탭 URL (필수)
--youtube-api-key   YouTube Data API v3 키 (필수)

# 범위 (셋 중 하나, --update-sheet 사용 시 불필요)
--popular START END
--latest  START END
--all

# 드라이브
--drive-folder      업로드할 드라이브 폴더 이름 (기본값: "쇼츠백업")

# 시트
--sheet-name        구글 시트 이름 (기본값: "쇼츠현황")

# 시트만 갱신 모드
--update-sheet      시트 이름 (이 옵션 사용 시 다운로드 생략)

# 선택
--ffmpeg-path       ffmpeg bin 폴더 경로 (기본: PATH 탐색)
```

---

## 4. shorts_manager.py 내부 구조

### 4-1. 상수/설정

```python
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_FILE = "token.json"
CREDS_FILE = "credentials.json"
```

### 4-2. 함수 목록 및 역할

#### Google 인증

```python
def get_google_services() -> tuple[DriveService, SheetsService]:
    """
    credentials.json으로 OAuth 인증.
    token.json 있으면 재사용, 만료 시 자동 갱신.
    Drive + Sheets 두 서비스를 동시에 반환.
    """
```

#### YouTube 데이터 수집

```python
def fetch_all_shorts_ids(channel_url: str) -> list[dict]:
    """
    yt-dlp --flat-playlist -J 로 채널 쇼츠 탭 전체 목록 수집.
    반환: [{"id": str, "title": str, "upload_date": str(YYYYMMDD)}, ...]
    upload_date: yt-dlp가 제공하는 값 그대로 사용
    """

def fetch_video_stats(video_ids: list[str], api_key: str) -> dict[str, dict]:
    """
    YouTube Data API v3 videos.list (part=statistics,snippet) 로
    50개씩 배치 조회.
    반환: {
        video_id: {
            "view_count": int,
            "like_count": int,   # 비공개면 0
            "comment_count": int,
            "title": str,        # API에서 가져온 원본 제목
            "published_at": str, # ISO8601
        }
    }
    """
```

#### 정렬 및 범위 선택

```python
def select_videos(
    items: list[dict],
    stats: dict[str, dict],
    mode: str,        # "popular" | "latest" | "all"
    start: int = 1,   # 1-based
    end: int = None,  # None이면 끝까지
) -> list[dict]:
    """
    mode="popular": stats의 view_count 기준 내림차순 정렬 후 [start-1:end] 슬라이스
    mode="latest":  items의 upload_date 기준 내림차순 정렬 후 [start-1:end] 슬라이스
    mode="all":     정렬 없이 전체 반환 (yt-dlp 순서 그대로)

    각 item에 rank 필드 추가 (전체 순위 기준, start부터 시작하는 번호)
    반환: [{"id", "title", "upload_date", "rank", "view_count", ...}, ...]
    """
```

#### Google Drive

```python
def get_or_create_folder(service, folder_name: str) -> str:
    """
    드라이브 루트에 folder_name 폴더 없으면 생성.
    폴더 ID 반환.
    """

def list_existing_files(service, folder_id: str) -> dict[str, str]:
    """
    폴더 내 파일 목록 조회.
    반환: {"파일명.mp4": "file_id", ...}
    drive link: https://drive.google.com/file/d/{file_id}/view
    """

def upload_file(
    service, filepath: str, filename: str, folder_id: str,
    max_retries: int = 5
) -> str:
    """
    resumable upload. 503/500 에러 시 지수 백오프로 재시도.
    반환: 업로드된 파일의 file_id
    """
```

#### 다운로드 및 변환

```python
def download_video(video_url: str, out_path: str, ffmpeg_bin: str = None) -> tuple[bool, str]:
    """
    yt-dlp로 다운로드. 검증된 포맷 옵션 사용:
      -S "vcodec:avc1,acodec:mp4a,res:1920,hdr:off"
      -f "bestvideo[width=1080][height=1920][vcodec^=avc1]+bestaudio[acodec^=mp4a]
          /bestvideo[width=1080][height=1920][vcodec^=avc1]+bestaudio
          /bestvideo[width=1080][height=1920]+bestaudio[acodec^=mp4a]
          /bestvideo[width=1080][height=1920]+bestaudio
          /bestvideo[width<=1080][height<=1920]+bestaudio[acodec^=mp4a]
          /bestvideo[width<=1080][height<=1920]+bestaudio
          /best"
      --merge-output-format mp4
      --remux-video mp4
    반환: (성공 여부, 에러 메시지)
    """

def get_video_codec(filepath: str, ffprobe_exe: str) -> str:
    """ffprobe로 비디오 코덱 확인. 반환: "h264" | "av1" | "vp9" | ..."""

def convert_to_h264(input_path: str, output_path: str, ffmpeg_exe: str) -> tuple[bool, str]:
    """
    AV1/VP9 → H.264 변환. 검증된 설정:
      -vf scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2
      -c:v libx264 -preset slow -crf 18 -profile:v high -level 4.2 -pix_fmt yuv420p
      -c:a aac -b:a 320k -movflags +faststart
    """
```

#### Google Sheets

```python
def get_or_create_sheet(sheets_service, sheet_name: str) -> tuple[str, str]:
    """
    Google Drive에서 sheet_name 이름의 구글 시트 검색.
    없으면 생성. 있으면 재사용.
    반환: (spreadsheet_id, sheet_url)
    """

def init_sheet_header(sheets_service, spreadsheet_id: str):
    """
    시트 1행에 헤더 작성 (없을 때만):
    번호 | 영상 제목 | 업로드 날짜 | 조회수 | 좋아요 수 | 댓글 수 | 유튜브 링크 | 드라이브 링크
    헤더 행은 굵게, 배경색 적용 (batchUpdate 사용)
    """

def upsert_sheet_rows(
    sheets_service, spreadsheet_id: str,
    rows: list[dict]
):
    """
    rows의 각 항목을 시트에 upsert:
    - 유튜브 링크(영상 ID)를 기준으로 기존 행 탐색
    - 있으면 조회수/좋아요/댓글수 업데이트
    - 없으면 맨 아래에 새 행 추가

    각 row dict:
    {
        "rank": int,
        "title": str,
        "published_at": str,   # YYYY-MM-DD 형식으로 변환해서 저장
        "view_count": int,
        "like_count": int,
        "comment_count": int,
        "youtube_url": str,    # https://www.youtube.com/shorts/{id}
        "drive_url": str,      # https://drive.google.com/file/d/{file_id}/view
                               # 다운로드 안 한 경우 빈 문자열
    }
    """

def update_sheet_stats(
    sheets_service, spreadsheet_id: str,
    stats: dict[str, dict]
):
    """
    --update-sheet 모드에서 사용.
    시트 전체 행을 읽어 유튜브 링크에서 video_id 추출,
    stats에서 최신 수치를 찾아 조회수/좋아요/댓글수 셀만 업데이트.
    """
```

#### 안전한 파일명

```python
def safe_filename(name: str, rank: int) -> str:
    """
    특수문자 제거 후 "{rank}. {title}.mp4" 형식으로 반환.
    rank는 실제 순위 (start부터 시작).
    최대 150자 제한.
    """
```

### 4-3. main() 흐름

#### 다운로드 모드

```
1. argparse로 인수 파싱 및 유효성 검사
   - --popular / --latest / --all 중 하나만 허용
   - START >= 1, END >= START 검증

2. get_google_services() → drive_service, sheets_service

3. fetch_all_shorts_ids(channel_url) → items
   - 진행 메시지: "[1/4] 채널 쇼츠 목록 수집 중... (총 N개)"

4. fetch_video_stats(video_ids, api_key) → stats
   - 진행 메시지: "[2/4] 영상 통계 조회 중... (N/M)"

5. select_videos(items, stats, mode, start, end) → selected
   - 진행 메시지: "[3/4] {start}~{end}위 선정 완료 (총 N개)"

6. get_or_create_folder(drive_service, drive_folder) → folder_id
7. list_existing_files(drive_service, folder_id) → existing
8. get_or_create_sheet(sheets_service, sheet_name) → spreadsheet_id
9. init_sheet_header(sheets_service, spreadsheet_id)

10. "[4/4] 다운로드 → 업로드 시작"
    tempfile.TemporaryDirectory() 안에서:
    for video in selected:
        filename = safe_filename(video["title"], video["rank"])
        
        if filename in existing:
            # 건너뛰되 시트는 업데이트 (드라이브 링크 채우기)
            drive_url = f"https://drive.google.com/file/d/{existing[filename]}/view"
            upsert_sheet_rows(...)
            continue
        
        download_video() → raw_path
        
        codec = get_video_codec(raw_path)
        if codec in ("av1", "vp9"):
            convert_to_h264(raw_path, conv_path)
            os.remove(raw_path)
            upload_path = conv_path
        else:
            upload_path = raw_path
        
        file_id = upload_file(drive_service, upload_path, filename, folder_id)
        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
        os.remove(upload_path)
        
        upsert_sheet_rows(sheets_service, spreadsheet_id, [{...}])

11. 완료 요약 출력:
    "완료. 업로드: N개 / 건너뜀: N개 / 실패: N개"
    "시트: {sheet_url}"
```

#### 시트 업데이트 모드 (--update-sheet)

```
1. argparse로 인수 파싱
2. get_google_services()
3. Drive에서 sheet_name 시트 찾기 → spreadsheet_id
4. 시트 전체 데이터 읽기 → 영상 ID 목록 추출
5. fetch_video_stats(video_ids, api_key) → stats
6. update_sheet_stats(sheets_service, spreadsheet_id, stats)
7. "완료. N개 행 업데이트됨" 출력
```

---

## 5. 에러 처리 원칙

- YouTube API 쿼터 초과 (403): 명확한 메시지 출력 후 종료
- 다운로드 실패: 해당 영상 건너뛰고 계속 진행, 실패 목록 마지막에 출력
- 업로드 실패 (503 등): 지수 백오프 5회 재시도
- SSL 에러: certifi 설치 안내 메시지 출력
- token.json 스코프 불일치: 자동으로 token.json 삭제 후 재인증 유도

---

## 6. README.md 구성

README는 다음 섹션을 포함해야 합니다:

### 6-1. 사전 준비 (Prerequisites)

#### 시스템 도구
- Python 3.9 이상
- ffmpeg 설치 방법 (Mac: `brew install ffmpeg` / Windows: gyan.dev 링크)
- yt-dlp 설치 방법 (`pip install yt-dlp`)

#### Google Cloud Console 설정 (단계별 스크린샷 없이 텍스트로)

**프로젝트 생성**
1. https://console.cloud.google.com 접속
2. 상단 프로젝트 선택 → "새 프로젝트" → 이름 입력 → 만들기

**API 사용 설정 (3개)**
1. "API 및 서비스" → "라이브러리"
2. 다음 3개 검색 후 각각 "사용" 클릭:
   - YouTube Data API v3
   - Google Drive API
   - Google Sheets API

**YouTube API 키 발급**
1. "API 및 서비스" → "사용자 인증 정보" → "사용자 인증 정보 만들기" → "API 키"
2. 생성된 키 복사 → `--youtube-api-key` 인수에 사용

**OAuth 클라이언트 ID 발급 (credentials.json)**
1. "사용자 인증 정보 만들기" → "OAuth 클라이언트 ID"
2. "OAuth 동의 화면" 먼저 구성 필요:
   - 앱 유형: 외부
   - 앱 이름: 아무거나
   - 테스트 사용자에 본인 Gmail 추가
3. 애플리케이션 유형: "데스크톱 앱" 선택
4. 생성 후 JSON 다운로드 → `credentials.json` 이름으로 스크립트와 같은 폴더에 저장

**Google Sheets API 범위 추가**
1. "API 및 서비스" → "Google Auth Platform" → "데이터 액세스" 탭
2. "직접 범위 추가" 텍스트박스에 입력:
   - `https://www.googleapis.com/auth/drive`
   - `https://www.googleapis.com/auth/spreadsheets`
3. 저장

### 6-2. 설치

```bash
git clone ...
cd ...
pip install -r requirements.txt
```

### 6-3. 사용법 (Usage)

각 CLI 예시와 설명.

### 6-4. 첫 실행 시 인증

처음 실행하면 브라우저가 자동으로 열림 → Google 계정 로그인 → 권한 승인.
이후 `token.json` 자동 생성, 재인증 불필요.

### 6-5. 주의사항

- `credentials.json`, `token.json`은 절대 공유/커밋하지 말 것 (.gitignore에 추가)
- YouTube API 무료 쿼터: 일일 10,000 유닛. `videos.list` 1회 = 1 유닛 (50개 영상).
  영상 6,000개 기준 약 120 유닛 소모 → 쿼터 문제 거의 없음.
- 구글드라이브 저장 용량 확인 필요 (영상 1개 약 30~100MB)

---

## 7. 구현 시 주의사항 (Claude Code에게)

1. **단일 파일**: `shorts_manager.py` 하나에 모든 로직을 담을 것. 외부 모듈 import 없음.
2. **Python 버전**: 3.9+ 호환 (union type hint `X | Y` 대신 `Optional[X]` 사용)
3. **인증 스코프**: Drive + Sheets를 하나의 OAuth flow로 동시 처리
4. **tempfile**: 로컬 저장 없이 `tempfile.TemporaryDirectory()` 안에서만 임시 파일 사용
5. **upsert 기준**: 유튜브 링크(video ID)를 고유 키로 사용. 제목이 바뀌어도 같은 영상이면 업데이트
6. **시트 정렬**: 시트 자체에 자동 정렬을 적용하지 말고, 행을 rank 기준으로 삽입. 사용자가 시트에서 직접 정렬하도록.
7. **좋아요 수 비공개**: like_count가 API 응답에 없으면 빈 문자열로 시트에 기록 (0이 아님)
8. **token.json 스코프 갱신**: 기존 token.json의 스코프가 현재 SCOPES와 다르면 자동 삭제 후 재인증
