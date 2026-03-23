"""
Downloads everything under /TMW/Music/ from Dropbox to ~/Music/TMW-Archive/,
preserving folder structure. Skips files that already exist with matching size.
Resumable: safe to re-run after interruption.
"""

import os
import time
import logging
from pathlib import Path

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata, FolderMetadata
from dotenv import load_dotenv

load_dotenv()

DROPBOX_SOURCE = "/TMW/Music"
LOCAL_DEST = Path("~/Music/TMW-Archive").expanduser()
RETRY_ATTEMPTS = 5
INITIAL_BACKOFF = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def build_client() -> dropbox.Dropbox:
    return dropbox.Dropbox(
        oauth2_refresh_token=os.environ["DROPBOX_REFRESH_TOKEN"],
        app_key=os.environ["DROPBOX_APP_KEY"],
        app_secret=os.environ["DROPBOX_APP_SECRET"],
    )


def list_all_files(dbx: dropbox.Dropbox, folder: str) -> list[FileMetadata]:
    files: list[FileMetadata] = []
    result = dbx.files_list_folder(folder, recursive=True)
    while True:
        for entry in result.entries:
            if isinstance(entry, FileMetadata):
                files.append(entry)
        if not result.has_more:
            break
        result = dbx.files_list_folder_continue(result.cursor)
    return files


def local_path(remote_path: str) -> Path:
    relative = remote_path[len(DROPBOX_SOURCE):]
    return LOCAL_DEST / relative.lstrip("/")


def download_file(dbx: dropbox.Dropbox, entry: FileMetadata, dest: Path) -> bool:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            _, response = dbx.files_download(entry.path_display)
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    f.write(chunk)
            tmp.rename(dest)
            return True
        except ApiError as e:
            if attempt == RETRY_ATTEMPTS:
                log.error("FAIL  %s — %s", entry.path_display, e)
                return False
            backoff = INITIAL_BACKOFF * (2 ** (attempt - 1))
            log.warning("Retry %d/%d for %s in %.0fs", attempt, RETRY_ATTEMPTS, entry.path_display, backoff)
            time.sleep(backoff)
    return False


def main() -> None:
    dbx = build_client()
    LOCAL_DEST.mkdir(parents=True, exist_ok=True)

    log.info("Listing files under %s …", DROPBOX_SOURCE)
    files = list_all_files(dbx, DROPBOX_SOURCE)
    log.info("Found %d files", len(files))

    downloaded = 0
    skipped = 0
    failed = 0
    bytes_downloaded = 0

    for i, entry in enumerate(files, 1):
        dest = local_path(entry.path_display)
        prefix = f"[{i}/{len(files)}]"

        if dest.exists() and dest.stat().st_size == entry.size:
            log.info("SKIP  %s %s", prefix, entry.path_display)
            skipped += 1
            continue

        log.info("DOWN  %s %s  (%.1f MB)", prefix, entry.path_display, entry.size / 1e6)
        success = download_file(dbx, entry, dest)
        if success:
            downloaded += 1
            bytes_downloaded += entry.size
        else:
            failed += 1

    log.info(
        "Done. Downloaded: %d  Skipped: %d  Failed: %d  Total: %.2f GB",
        downloaded,
        skipped,
        failed,
        bytes_downloaded / 1e9,
    )


if __name__ == "__main__":
    main()
