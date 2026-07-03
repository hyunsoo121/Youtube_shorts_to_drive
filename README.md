# YouTube Shorts Manager

특정 YouTube 채널의 Shorts 영상을 인기순/최신순/전체로 다운로드하여 Google Drive에 업로드하고,
Google Sheets에 조회수·좋아요·댓글수 현황을 자동으로 기록하는 CLI 도구입니다.

---

## 1. 사전 준비 (Prerequisites)

### 시스템 도구

- Python 3.9 이상
- ffmpeg
  - Mac: `brew install ffmpeg`
  - Windows: 아래 "Windows에서 PATH 설정하기" 참고
- yt-dlp (`requirements.txt`에 포함되어 함께 설치됩니다. 최신 버전 유지를 원하면 `pip install -U yt-dlp`)

### Windows에서 PATH 설정하기

Windows는 Mac(`brew install ffmpeg`)처럼 한 줄로 설치되지 않아서, ffmpeg를 받은 뒤 실행 파일 위치를 직접 PATH에 등록해야 합니다.

1. [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)에서 "release essentials" 빌드(zip)를 다운로드
2. 압축을 원하는 위치에 풀기 (예: `C:\ffmpeg`). 압축을 풀면 안에 `bin` 폴더가 있고, 그 안에 `ffmpeg.exe`, `ffprobe.exe`가 있습니다. 이 `bin` 폴더 경로(예: `C:\ffmpeg\bin`)를 기억해두세요.
3. PATH에 추가하기:
   - Windows 검색창에 "환경 변수" 입력 → "시스템 환경 변수 편집" 실행
   - "환경 변수" 버튼 클릭
   - "사용자 변수"(또는 "시스템 변수") 목록에서 `Path` 선택 → "편집"
   - "새로 만들기" → 위에서 기억한 `bin` 폴더 경로(예: `C:\ffmpeg\bin`) 입력 → 확인을 눌러 모든 창 닫기
4. **열려 있던 명령 프롬프트/PowerShell/터미널을 모두 닫고 새로 열어야** 변경사항이 적용됩니다.
5. 새 터미널에서 `ffmpeg -version`을 입력했을 때 버전 정보가 출력되면 PATH 설정이 완료된 것입니다.

같은 방식으로 Python이나 다른 실행 파일도 PATH에 등록할 수 있습니다 (Python 설치 시 "Add python.exe to PATH" 체크박스를 선택했다면 이 과정이 자동으로 됩니다).

PATH 설정이 번거롭거나 시스템 설정을 건드리고 싶지 않다면, 아래 "3-5. 전체 인수 목록"에 있는 `--ffmpeg-path` 옵션으로 PATH 등록 없이 바로 사용할 수도 있습니다.

### Google Cloud Console 설정

**1) 프로젝트 생성**

1. https://console.cloud.google.com 접속
2. 상단 프로젝트 선택 → "새 프로젝트" → 이름 입력 → 만들기

**2) API 사용 설정 (3개)**

1. "API 및 서비스" → "라이브러리"
2. 다음 3개를 검색해서 각각 "사용" 클릭
   - YouTube Data API v3
   - Google Drive API
   - Google Sheets API

**3) YouTube API 키 발급**

1. "API 및 서비스" → "사용자 인증 정보" → "사용자 인증 정보 만들기" → "API 키"
2. 생성된 키를 복사해서 `--youtube-api-key` 인수에 사용

**4) OAuth 클라이언트 ID 발급 (credentials.json)**

1. "사용자 인증 정보 만들기" → "OAuth 클라이언트 ID"
2. "OAuth 동의 화면"을 먼저 구성해야 합니다:
   - 앱 유형: 외부
   - 앱 이름: 아무거나
   - 테스트 사용자에 본인 Gmail 계정 추가
3. 애플리케이션 유형: "데스크톱 앱" 선택
4. 생성 후 JSON을 다운로드하여 `credentials.json`이라는 이름으로 스크립트와 같은 폴더에 저장

**5) Google Sheets API 범위 추가**

1. "API 및 서비스" → "Google Auth Platform" → "데이터 액세스" 탭
2. "직접 범위 추가" 텍스트박스에 아래 두 줄을 입력
   - `https://www.googleapis.com/auth/drive`
   - `https://www.googleapis.com/auth/spreadsheets`
3. 저장

---

## 2. 설치

```bash
git clone https://github.com/hyunsoo121/Youtube_shorts_to_drive.git
cd Youtube_shorts_to_drive
pip install -r requirements.txt
```

`credentials.json`을 앞서 발급받은 파일로 교체해 프로젝트 폴더에 둡니다.

---

## 3. 사용법 (Usage)

### 3-1. 다운로드 + 시트 생성/업데이트

```bash
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --popular 1 100 \
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

START, END는 1-based index이며 END는 포함됩니다.

### 3-3. 시트만 업데이트 (다운로드 없이 수치만 갱신)

```bash
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --update-sheet "쇼츠현황" \
  --youtube-api-key "AIza..."
```

### 3-4. 명령어 예시 모음

```bash
# 시트만 업데이트 (다운로드 없이 조회수/좋아요/댓글수만 갱신)
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --update-sheet "쇼츠현황" \
  --youtube-api-key "AIza..."

# 전체 영상 다운로드
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --all \
  --drive-folder "쇼츠백업" \
  --sheet-name "쇼츠현황" \
  --youtube-api-key "AIza..."

# 인기순 1~100위 다운로드
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --popular 1 100 \
  --drive-folder "쇼츠백업" \
  --sheet-name "쇼츠현황" \
  --youtube-api-key "AIza..."

# 인기순 50~100위만 다운로드 (이미 1~49위를 받아둔 상태에서 이어받을 때)
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --popular 50 100 \
  --drive-folder "쇼츠백업" \
  --sheet-name "쇼츠현황" \
  --youtube-api-key "AIza..."

# 최신순 1~100위 다운로드
python3 shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --latest 1 100 \
  --drive-folder "쇼츠백업" \
  --sheet-name "쇼츠현황" \
  --youtube-api-key "AIza..."
```

Windows에서는 `python3` 대신 `python`을 사용하세요 (예: `python shorts_manager.py ...`).

### 3-5. 전체 인수 목록

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

#### `--ffmpeg-path`는 언제 쓰나요?

기본적으로 스크립트는 시스템 PATH에서 `ffmpeg`/`ffprobe`를 찾습니다. 아래 상황에서는 `--ffmpeg-path`로 ffmpeg의 `bin` 폴더 경로를 직접 지정하세요.

- 위 "Windows에서 PATH 설정하기"를 따라하지 않고, PATH 등록 없이 압축만 풀어서 바로 쓰고 싶을 때
- 회사/공용 PC라 시스템 환경 변수를 수정할 권한이 없을 때
- 여러 버전의 ffmpeg가 설치되어 있어서 특정 버전을 지정해서 써야 할 때
- PATH에 막 추가했는데 터미널을 재시작하지 않아 아직 인식이 안 될 때 (재시작 없이 바로 실행하고 싶은 경우)

사용 예 (Windows, ffmpeg를 `C:\ffmpeg\bin`에 풀어둔 경우):

```bash
python shorts_manager.py \
  --channel "https://www.youtube.com/@채널핸들/shorts" \
  --popular 1 100 \
  --youtube-api-key "AIza..." \
  --ffmpeg-path "C:\ffmpeg\bin"
```

---

## 4. 첫 실행 시 인증

처음 실행하면 브라우저가 자동으로 열립니다 → Google 계정 로그인 → 권한 승인.
이후 `token.json`이 자동 생성되어 재인증이 필요하지 않습니다.

---

## 5. 주의사항

- `credentials.json`, `token.json`은 절대 공유하거나 커밋하지 마세요 (`.gitignore`에 이미 추가되어 있습니다).
- YouTube API 무료 쿼터는 일일 10,000 유닛입니다. `videos.list` 1회 = 1 유닛(영상 50개 기준).
  영상 6,000개를 조회해도 약 120 유닛만 소모되므로 쿼터 문제는 거의 없습니다.
- Google Drive 저장 용량을 미리 확인하세요 (영상 1개당 약 30~100MB).
- YouTube API 403(쿼터 초과) 발생 시 안내 메시지를 출력하고 즉시 종료합니다.
- 개별 영상 다운로드가 실패해도 전체 작업은 계속 진행되며, 실패 목록은 마지막에 출력됩니다.
- 업로드 실패(503 등)는 지수 백오프로 최대 5회 재시도합니다.
- `token.json`의 인증 범위가 현재 스코프와 다르면 자동으로 삭제 후 재인증을 요청합니다.
