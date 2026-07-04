#!/usr/bin/env python3
"""YouTube Shorts를 다운로드하여 Google Drive에 업로드하고 Google Sheets에 현황을 기록하는 CLI."""

import argparse
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# Windows에서 콘솔/리다이렉트 출력이 CP949 등으로 잡혀 한글 로그가 깨지는 것을 방지.
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# 리소스 고갈(포트 부족 등) 같은 일시적 네트워크 오류. 지수 백오프로 재시도한다.
_TRANSIENT_NETWORK_ERRORS = (OSError, ConnectionError, TimeoutError, ssl.SSLError, http.client.HTTPException)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_FILE = "token.json"
CREDS_FILE = "credentials.json"

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3/videos"

FORMAT_SORT = "vcodec:avc1,acodec:mp4a,res:1920,hdr:off"
FORMAT_SELECTOR = (
    "bestvideo[width=1080][height=1920][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
    "/bestvideo[width=1080][height=1920][vcodec^=avc1]+bestaudio"
    "/bestvideo[width=1080][height=1920]+bestaudio[acodec^=mp4a]"
    "/bestvideo[width=1080][height=1920]+bestaudio"
    "/bestvideo[width<=1080][height<=1920]+bestaudio[acodec^=mp4a]"
    "/bestvideo[width<=1080][height<=1920]+bestaudio"
    "/best"
)

SHEET_HEADER = [
    "번호", "영상 제목", "업로드 날짜", "조회수", "좋아요 수", "댓글 수", "유튜브 링크", "드라이브 링크",
]


def _get_with_retry(url: str, params: dict, max_retries: int = 5, timeout: int = 30):
    """requests.get 실행. 5xx/일시적 네트워크 오류는 지수 백오프로 재시도."""
    retry = 0
    while True:
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code in (500, 502, 503, 504) and retry < max_retries:
                wait = 2 ** retry
                print(f"    API 오류({resp.status_code}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            return resp
        except (requests.exceptions.RequestException, *_TRANSIENT_NETWORK_ERRORS) as e:
            if retry < max_retries:
                wait = 2 ** retry
                print(f"    네트워크 오류({e}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            raise


def _execute_with_retry(request, max_retries: int = 5):
    """Google API request 실행. 429(쿼터 초과)/5xx/일시적 네트워크 오류는 지수 백오프로 재시도.
    429는 분당 쿼터가 리셋될 때까지 기다려야 하므로 더 길게 대기한다."""
    retry = 0
    while True:
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 429 and retry < max_retries:
                wait = 10 * (2 ** retry)
                print(f"    API 쿼터 초과(429), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            if e.resp.status in (500, 502, 503, 504) and retry < max_retries:
                wait = 2 ** retry
                print(f"    API 오류({e.resp.status}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            raise
        except _TRANSIENT_NETWORK_ERRORS as e:
            if retry < max_retries:
                wait = 2 ** retry
                print(f"    네트워크 오류({e}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            raise


# ---------------------------------------------------------------------------
# Google 인증
# ---------------------------------------------------------------------------

def get_google_services():
    """credentials.json으로 OAuth 인증. token.json 있으면 재사용, 만료 시 자동 갱신.
    Drive + Sheets 두 서비스를 동시에 반환."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None
        if creds and set(creds.scopes or []) != set(SCOPES):
            print("token.json의 인증 범위가 변경되어 재인증이 필요합니다.")
            os.remove(TOKEN_FILE)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds or not creds.valid:
            if not os.path.exists(CREDS_FILE):
                print(f"오류: {CREDS_FILE} 파일이 없습니다. README.md의 안내에 따라 발급받으세요.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    drive_service = build("drive", "v3", credentials=creds)
    sheets_service = build("sheets", "v4", credentials=creds)
    return drive_service, sheets_service


# ---------------------------------------------------------------------------
# YouTube 데이터 수집
# ---------------------------------------------------------------------------

def fetch_all_shorts_ids(channel_url: str) -> List[Dict]:
    """yt-dlp --flat-playlist -J 로 채널 쇼츠 탭 전체 목록 수집."""
    cmd = ["yt-dlp", "--flat-playlist", "-J", channel_url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        print("오류: yt-dlp가 설치되어 있지 않습니다. `pip install yt-dlp`로 설치하세요.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"오류: 채널 쇼츠 목록 수집 실패\n{e.stderr}")
        sys.exit(1)

    data = json.loads(result.stdout)
    entries = data.get("entries") or []
    items = []
    for entry in entries:
        if not entry:
            continue
        items.append({
            "id": entry.get("id"),
            "title": entry.get("title") or "",
            "upload_date": entry.get("upload_date") or "",
        })
    return items


def fetch_video_stats(video_ids: List[str], api_key: str) -> Dict[str, Dict]:
    """YouTube Data API v3 videos.list (part=statistics,snippet) 로 50개씩 배치 조회."""
    stats: Dict[str, Dict] = {}
    total = len(video_ids)
    for i in range(0, total, 50):
        batch = video_ids[i:i + 50]
        params = {
            "part": "statistics,snippet",
            "id": ",".join(batch),
            "key": api_key,
        }
        resp = _get_with_retry(YOUTUBE_API_BASE, params)

        if resp.status_code == 403:
            reason = ""
            try:
                reason = resp.json()["error"]["errors"][0]["reason"]
            except Exception:
                pass
            if reason == "quotaExceeded":
                print("오류: YouTube API 일일 쿼터를 초과했습니다. 내일 다시 시도하세요.")
            else:
                print(f"오류: YouTube API 접근이 거부되었습니다.\n{resp.text}")
            sys.exit(1)
        if resp.status_code >= 400:
            print(f"오류: YouTube API 요청이 실패했습니다 (상태 코드 {resp.status_code}).\n{resp.text}")
            sys.exit(1)

        data = resp.json()
        for item in data.get("items", []):
            vid = item["id"]
            statistics = item.get("statistics", {})
            snippet = item.get("snippet", {})
            stats[vid] = {
                "view_count": int(statistics.get("viewCount", 0)),
                "like_count": int(statistics["likeCount"]) if "likeCount" in statistics else None,
                "comment_count": int(statistics.get("commentCount", 0)),
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
            }
        print(f"[2/4] 영상 통계 조회 중... ({min(i + 50, total)}/{total})")
    return stats


# ---------------------------------------------------------------------------
# 정렬 및 범위 선택
# ---------------------------------------------------------------------------

def select_videos(
    items: List[Dict],
    stats: Dict[str, Dict],
    mode: str,
    start: int = 1,
    end: Optional[int] = None,
) -> List[Dict]:
    """mode에 따라 정렬 후 [start-1:end] 슬라이스. rank 필드를 추가한다."""
    merged = []
    for it in items:
        vid = it.get("id")
        if not vid or vid not in stats:
            continue
        s = stats[vid]
        merged.append({
            "id": vid,
            "title": s.get("title") or it.get("title") or "",
            "upload_date": it.get("upload_date") or "",
            "view_count": s.get("view_count", 0),
            "like_count": s.get("like_count"),
            "comment_count": s.get("comment_count", 0),
            "published_at": s.get("published_at", ""),
        })

    if mode == "popular":
        merged.sort(key=lambda x: x["view_count"], reverse=True)
        selected = merged[start - 1:end]
    elif mode == "latest":
        merged.sort(key=lambda x: x["upload_date"], reverse=True)
        selected = merged[start - 1:end]
    elif mode == "all":
        selected = merged
        start = 1
    else:
        raise ValueError(f"알 수 없는 mode: {mode}")

    for idx, v in enumerate(selected):
        v["rank"] = start + idx
    return selected


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------

def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def get_or_create_folder(service, folder_name: str) -> str:
    """드라이브 루트에 folder_name 폴더 없으면 생성. 폴더 ID 반환."""
    query = (
        f"name = '{_escape_query_value(folder_name)}' and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "'root' in parents and trashed = false"
    )
    res = _execute_with_retry(service.files().list(q=query, spaces="drive", fields="files(id, name)"))
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root"],
    }
    folder = _execute_with_retry(service.files().create(body=metadata, fields="id"))
    return folder["id"]


def list_existing_files(service, folder_id: str) -> Dict[str, str]:
    """폴더 내 파일 목록 조회. 파일에 저장된 video_id 속성 기준으로 매핑.
    반환: {video_id: file_id}"""
    files: Dict[str, str] = {}
    page_token = None
    while True:
        res = _execute_with_retry(service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id, name, properties)",
            pageToken=page_token,
        ))
        for f in res.get("files", []):
            vid = (f.get("properties") or {}).get("video_id")
            if vid:
                files[vid] = f["id"]
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return files


def upload_file(
    service, filepath: str, filename: str, folder_id: str, video_id: str, max_retries: int = 5
) -> str:
    """resumable upload. video_id를 파일 속성(properties)에 저장해 이후 중복 판별에 사용.
    503/500 에러 시 지수 백오프로 재시도."""
    media = MediaFileUpload(filepath, resumable=True)
    metadata = {
        "name": filename,
        "parents": [folder_id],
        "properties": {"video_id": video_id},
    }
    request = service.files().create(body=metadata, media_body=media, fields="id")

    response = None
    retry = 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry < max_retries:
                wait = 2 ** retry
                print(f"    업로드 오류({e.resp.status}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            raise
        except _TRANSIENT_NETWORK_ERRORS as e:
            if retry < max_retries:
                wait = 2 ** retry
                print(f"    네트워크 오류({e}), {wait}초 후 재시도... ({retry + 1}/{max_retries})")
                time.sleep(wait)
                retry += 1
                continue
            raise
    return response["id"]


# ---------------------------------------------------------------------------
# 다운로드 및 변환
# ---------------------------------------------------------------------------

def download_video(video_url: str, out_path: str, ffmpeg_bin: Optional[str] = None) -> Tuple[bool, str]:
    """yt-dlp로 다운로드. 검증된 포맷 옵션 사용."""
    cmd = [
        "yt-dlp",
        "-S", FORMAT_SORT,
        "-f", FORMAT_SELECTOR,
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "-o", out_path,
        video_url,
    ]
    if ffmpeg_bin:
        cmd += ["--ffmpeg-location", ffmpeg_bin]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip()[-500:]
    return True, ""


def get_video_codec(filepath: str, ffprobe_exe: str = "ffprobe") -> str:
    """ffprobe로 비디오 코덱 확인. 반환: "h264" | "av1" | "vp9" | ..."""
    cmd = [
        ffprobe_exe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        filepath,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    try:
        data = json.loads(result.stdout)
        return data["streams"][0]["codec_name"]
    except (KeyError, IndexError, json.JSONDecodeError):
        return ""


def convert_to_h264(input_path: str, output_path: str, ffmpeg_exe: str = "ffmpeg") -> Tuple[bool, str]:
    """AV1/VP9 → H.264 변환."""
    cmd = [
        ffmpeg_exe, "-y", "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "320k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip()[-500:]
    return True, ""


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def get_or_create_sheet(drive_service, sheets_service, sheet_name: str) -> Tuple[str, str]:
    """Google Drive에서 sheet_name 이름의 구글 시트 검색. 없으면 생성."""
    query = (
        f"name = '{_escape_query_value(sheet_name)}' and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )
    res = _execute_with_retry(drive_service.files().list(q=query, spaces="drive", fields="files(id, name)"))
    files = res.get("files", [])
    if files:
        spreadsheet_id = files[0]["id"]
    else:
        spreadsheet = _execute_with_retry(sheets_service.spreadsheets().create(
            body={"properties": {"title": sheet_name}}, fields="spreadsheetId"
        ))
        spreadsheet_id = spreadsheet["spreadsheetId"]

    sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    return spreadsheet_id, sheet_url


FILTER_START_COL = SHEET_HEADER.index("영상 제목")
FILTER_END_COL = SHEET_HEADER.index("댓글 수") + 1


def init_sheet_header(sheets_service, spreadsheet_id: str):
    """시트 1행에 헤더 작성 (없을 때만). 헤더 서식, 헤더 행 고정,
    영상 제목~댓글 수 구간에 정렬/필터 화살표를 함께 설정한다."""
    result = _execute_with_retry(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="A1:H1"
    ))
    existing = result.get("values", [])
    if existing and existing[0]:
        return

    _execute_with_retry(sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range="A1:H1",
        valueInputOption="RAW", body={"values": [SHEET_HEADER]},
    ))

    _execute_with_retry(sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {
                                    "bold": True,
                                    "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                                },
                                "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": 0,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "setBasicFilter": {
                        "filter": {
                            "range": {
                                "sheetId": 0,
                                "startRowIndex": 0,
                                "startColumnIndex": FILTER_START_COL,
                                "endColumnIndex": FILTER_END_COL,
                            }
                        }
                    }
                },
            ]
        },
    ))


def _extract_video_id(youtube_url: str) -> Optional[str]:
    m = re.search(r"shorts/([\w-]+)", youtube_url or "")
    return m.group(1) if m else None


def _safe_text(value: str) -> str:
    """USER_ENTERED로 쓸 때 =,+,-,@로 시작하는 문자열이 수식으로 오인되지 않도록 방어."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _parse_row_bounds(a1_range: str) -> Tuple[int, int]:
    """"'시트1'!A4:H6" 형태의 updatedRange에서 (startRowIndex, endRowIndex)를 0-based로 반환."""
    cell_range = a1_range.split("!")[-1]
    start_cell, end_cell = cell_range.split(":")
    start_row = int(re.search(r"\d+", start_cell).group())
    end_row = int(re.search(r"\d+", end_cell).group())
    return start_row - 1, end_row


def _reset_row_format(sheets_service, spreadsheet_id: str, start_row_index: int, end_row_index: int):
    """새로 추가된 행이 헤더 서식(굵게/회색)을 상속받지 않도록 기본 서식으로 재설정."""
    _execute_with_retry(sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{
                "repeatCell": {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": start_row_index,
                        "endRowIndex": end_row_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "bold": False,
                                "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                            },
                            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            }]
        },
    ))


def build_id_to_row_cache(sheets_service, spreadsheet_id: str) -> Dict[str, int]:
    """시트 전체를 한 번만 읽어 {video_id: row_number} 캐시를 만든다.
    영상마다 매번 시트를 새로 읽으면 Sheets API 읽기 쿼터(분당 요청 수)를 금방 초과하므로,
    호출자가 이 캐시를 만들어 upsert_sheet_rows()에 계속 넘겨써야 한다."""
    result = _execute_with_retry(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="A2:H"
    ))
    existing_rows = result.get("values", [])

    id_to_row = {}
    for idx, row in enumerate(existing_rows):
        if len(row) >= 7:
            vid = _extract_video_id(row[6])
            if vid:
                id_to_row[vid] = idx + 2  # 1-based, header가 1행
    return id_to_row


def upsert_sheet_rows(sheets_service, spreadsheet_id: str, rows: List[Dict], id_to_row: Dict[str, int]):
    """rows의 각 항목을 유튜브 링크(video ID) 기준으로 upsert.
    id_to_row: build_id_to_row_cache()로 만든 캐시. 시트를 다시 읽지 않고 이 캐시만 참조하며,
    새로 추가된 행은 이 함수가 캐시에 반영한다."""
    update_data = []
    append_rows = []
    appended_vids = []
    for r in rows:
        vid = _extract_video_id(r["youtube_url"])
        like = r.get("like_count")
        like_val = "" if like is None else like

        if vid in id_to_row:
            row_num = id_to_row[vid]
            update_data.append({
                "range": f"D{row_num}:F{row_num}",
                "values": [[r["view_count"], like_val, r["comment_count"]]],
            })
            if r.get("drive_url"):
                update_data.append({
                    "range": f"H{row_num}",
                    "values": [[r["drive_url"]]],
                })
        else:
            append_rows.append([
                "=ROW()-1", _safe_text(r["title"]), r["published_at"],
                r["view_count"], like_val, r["comment_count"],
                r["youtube_url"], r.get("drive_url", ""),
            ])
            appended_vids.append(vid)

    if update_data:
        # H열(드라이브 링크)이 자동으로 하이퍼링크 처리되도록 USER_ENTERED 사용.
        _execute_with_retry(sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": update_data},
        ))

    if append_rows:
        # 번호 열의 "=ROW()-1" 수식이 평가되도록 USER_ENTERED 사용.
        response = _execute_with_retry(sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range="A1:H1",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": append_rows},
        ))
        # 새 행이 삽입 시 위 행의 서식(굵게/회색)을 상속받으므로 기본 서식으로 되돌린다.
        start_row_index, end_row_index = _parse_row_bounds(response["updates"]["updatedRange"])
        _reset_row_format(sheets_service, spreadsheet_id, start_row_index, end_row_index)
        for offset, vid in enumerate(appended_vids):
            id_to_row[vid] = start_row_index + 1 + offset  # 1-based row number


def update_sheet_stats(sheets_service, spreadsheet_id: str, stats: Dict[str, Dict]) -> int:
    """--update-sheet 모드에서 사용. 조회수/좋아요/댓글수 셀만 업데이트."""
    result = _execute_with_retry(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="A2:H"
    ))
    existing_rows = result.get("values", [])

    update_data = []
    updated = 0
    for idx, row in enumerate(existing_rows):
        if len(row) < 7:
            continue
        vid = _extract_video_id(row[6])
        if not vid or vid not in stats:
            continue
        s = stats[vid]
        like = s.get("like_count")
        like_val = "" if like is None else like
        row_num = idx + 2
        update_data.append({
            "range": f"D{row_num}:F{row_num}",
            "values": [[s.get("view_count", 0), like_val, s.get("comment_count", 0)]],
        })
        updated += 1

    if update_data:
        _execute_with_retry(sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": update_data},
        ))
    return updated


# ---------------------------------------------------------------------------
# 안전한 파일명
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """특수문자 제거 후 "{title}.mp4" 형식으로 반환. 최대 150자 제한."""
    cleaned = re.sub(r'[\\/:*?"<>|]', "", name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    suffix = ".mp4"
    filename = f"{cleaned}{suffix}"
    if len(filename) > 150:
        max_title_len = 150 - len(suffix)
        cleaned = cleaned[:max_title_len].rstrip()
        filename = f"{cleaned}{suffix}"
    return filename


def _format_date(value: Optional[str]) -> str:
    """YYYYMMDD 또는 ISO8601 문자열을 YYYY-MM-DD로 변환."""
    if not value:
        return ""
    if re.match(r"^\d{8}$", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    if "T" in value:
        return value.split("T")[0]
    return value


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _try_upsert(
    sheets_service, spreadsheet_id: str, row: Dict, filename: str, failed: List[str],
    id_to_row: Dict[str, int],
) -> bool:
    """upsert_sheet_rows 실패(네트워크 오류 등)가 전체 배치를 중단시키지 않도록 격리."""
    try:
        upsert_sheet_rows(sheets_service, spreadsheet_id, [row], id_to_row)
        return True
    except Exception as e:
        print(f"  [실패] {filename}: 시트 갱신 실패 - {e}")
        failed.append(f"{filename}: 시트 갱신 실패 - {e}")
        return False


def run_download_mode(args, mode: str, start: int, end: Optional[int]):
    ffmpeg_bin = args.ffmpeg_path
    ffmpeg_exe = os.path.join(ffmpeg_bin, "ffmpeg") if ffmpeg_bin else "ffmpeg"
    ffprobe_exe = os.path.join(ffmpeg_bin, "ffprobe") if ffmpeg_bin else "ffprobe"

    drive_service, sheets_service = get_google_services()

    print("[1/4] 채널 쇼츠 목록 수집 중...")
    items = fetch_all_shorts_ids(args.channel)
    print(f"[1/4] 채널 쇼츠 목록 수집 완료 (총 {len(items)}개)")

    video_ids = [it["id"] for it in items if it.get("id")]
    print("[2/4] 영상 통계 조회 중...")
    stats = fetch_video_stats(video_ids, args.youtube_api_key)

    selected = select_videos(items, stats, mode, start, end)
    range_desc = "전체" if mode == "all" else f"{start}~{end}위"
    print(f"[3/4] {range_desc} 선정 완료 (총 {len(selected)}개)")

    folder_id = get_or_create_folder(drive_service, args.drive_folder)
    existing = list_existing_files(drive_service, folder_id)
    spreadsheet_id, sheet_url = get_or_create_sheet(drive_service, sheets_service, args.sheet_name)
    init_sheet_header(sheets_service, spreadsheet_id)
    id_to_row = build_id_to_row_cache(sheets_service, spreadsheet_id)

    print("[4/4] 다운로드 → 업로드 시작")
    uploaded, skipped, failed = 0, 0, []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for video in selected:
            filename = safe_filename(video["title"])
            youtube_url = f"https://www.youtube.com/shorts/{video['id']}"
            published_at = _format_date(video.get("published_at") or video.get("upload_date"))

            if video["id"] in existing:
                drive_url = f"https://drive.google.com/file/d/{existing[video['id']]}/view"
                if _try_upsert(sheets_service, spreadsheet_id, {
                    "title": video["title"],
                    "published_at": published_at,
                    "view_count": video["view_count"],
                    "like_count": video.get("like_count"),
                    "comment_count": video["comment_count"],
                    "youtube_url": youtube_url,
                    "drive_url": drive_url,
                }, filename, failed, id_to_row):
                    skipped += 1
                    print(f"  [건너뜀] {filename}")
                continue

            raw_path = os.path.join(tmp_dir, f"raw_{video['id']}.mp4")
            ok, err = download_video(youtube_url, raw_path, ffmpeg_bin)
            if not ok:
                print(f"  [실패] {filename}: {err}")
                failed.append(f"{filename}: {err}")
                continue

            codec = get_video_codec(raw_path, ffprobe_exe)
            if codec in ("av1", "vp9"):
                conv_path = os.path.join(tmp_dir, f"conv_{video['id']}.mp4")
                ok, err = convert_to_h264(raw_path, conv_path, ffmpeg_exe)
                os.remove(raw_path)
                if not ok:
                    print(f"  [실패] {filename}: 코덱 변환 실패 - {err}")
                    failed.append(f"{filename}: 코덱 변환 실패 - {err}")
                    continue
                upload_path = conv_path
            else:
                upload_path = raw_path

            try:
                file_id = upload_file(drive_service, upload_path, filename, folder_id, video["id"])
            except Exception as e:
                print(f"  [실패] {filename}: 업로드 실패 - {e}")
                failed.append(f"{filename}: 업로드 실패 - {e}")
                os.remove(upload_path)
                continue

            drive_url = f"https://drive.google.com/file/d/{file_id}/view"
            os.remove(upload_path)

            if _try_upsert(sheets_service, spreadsheet_id, {
                "title": video["title"],
                "published_at": published_at,
                "view_count": video["view_count"],
                "like_count": video.get("like_count"),
                "comment_count": video["comment_count"],
                "youtube_url": youtube_url,
                "drive_url": drive_url,
            }, filename, failed, id_to_row):
                uploaded += 1
                print(f"  [완료] {filename}")

    print(f"완료. 업로드: {uploaded}개 / 건너뜀: {skipped}개 / 실패: {len(failed)}개")
    if failed:
        print("실패 목록:")
        for f in failed:
            print(f"  - {f}")
    print(f"시트: {sheet_url}")


def run_update_sheet_mode(args):
    drive_service, sheets_service = get_google_services()

    query = (
        f"name = '{_escape_query_value(args.update_sheet)}' and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )
    res = _execute_with_retry(drive_service.files().list(q=query, spaces="drive", fields="files(id, name)"))
    files = res.get("files", [])
    if not files:
        print(f"오류: '{args.update_sheet}' 이름의 시트를 찾을 수 없습니다.")
        sys.exit(1)
    spreadsheet_id = files[0]["id"]

    result = _execute_with_retry(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="A2:H"
    ))
    rows = result.get("values", [])
    video_ids = []
    for row in rows:
        if len(row) >= 7:
            vid = _extract_video_id(row[6])
            if vid:
                video_ids.append(vid)

    stats = fetch_video_stats(video_ids, args.youtube_api_key)
    updated = update_sheet_stats(sheets_service, spreadsheet_id, stats)
    print(f"완료. {updated}개 행 업데이트됨")


def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts를 다운로드하여 Drive/Sheets에 정리합니다.")
    parser.add_argument("--channel", required=True, help="채널 Shorts 탭 URL")
    parser.add_argument("--youtube-api-key", required=True, help="YouTube Data API v3 키")

    range_group = parser.add_mutually_exclusive_group()
    range_group.add_argument("--popular", nargs=2, type=int, metavar=("START", "END"), help="인기순 START~END번째")
    range_group.add_argument("--latest", nargs=2, type=int, metavar=("START", "END"), help="최신순 START~END번째")
    range_group.add_argument("--all", action="store_true", help="전체 다운로드")

    parser.add_argument("--drive-folder", default="쇼츠백업", help="업로드할 드라이브 폴더 이름")
    parser.add_argument("--sheet-name", default="쇼츠현황", help="구글 시트 이름")
    parser.add_argument("--update-sheet", metavar="SHEET_NAME", help="시트만 갱신 (다운로드 생략)")
    parser.add_argument("--ffmpeg-path", default=None, help="ffmpeg bin 폴더 경로 (기본: PATH 탐색)")

    args = parser.parse_args()

    if args.update_sheet:
        run_update_sheet_mode(args)
        return

    if args.popular:
        mode, start, end = "popular", args.popular[0], args.popular[1]
    elif args.latest:
        mode, start, end = "latest", args.latest[0], args.latest[1]
    elif args.all:
        mode, start, end = "all", 1, None
    else:
        parser.error("--popular, --latest, --all 중 하나를 지정하거나 --update-sheet을 사용하세요.")
        return

    if mode != "all":
        if start < 1:
            parser.error("START는 1 이상이어야 합니다.")
        if end < start:
            parser.error("END는 START 이상이어야 합니다.")

    run_download_mode(args, mode, start, end)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        sys.exit(1)
    except requests.exceptions.SSLError:
        print("SSL 오류가 발생했습니다. `pip install --upgrade certifi` 실행 후 다시 시도하세요.")
        sys.exit(1)
