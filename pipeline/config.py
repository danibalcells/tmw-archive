import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ARCHIVE_ROOT: Path = Path(os.environ.get("ARCHIVE_ROOT", "~/Music/TMW-Archive")).expanduser()

DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///data/archive.db")

# "local" | "s3" | "gcs"
STORAGE_BACKEND: str = os.environ.get("STORAGE_BACKEND", "local")

_S3_BUCKET: str = os.environ.get("S3_BUCKET", "")
_S3_PREFIX: str = os.environ.get("S3_PREFIX", "").strip("/")
_GCS_BUCKET: str = os.environ.get("GCS_BUCKET", "")
_GCS_PREFIX: str = os.environ.get("GCS_PREFIX", "").strip("/")


def resolve_audio_url(relative_path: str) -> str:
    """Return a URL or absolute path for a source_path stored in the DB.

    Local: absolute filesystem path (the frontend API serves it as a static file).
    S3/GCS: object URL — swap for a signed URL generator when auth is needed.
    """
    if STORAGE_BACKEND == "s3":
        key = f"{_S3_PREFIX}/{relative_path}" if _S3_PREFIX else relative_path
        return f"https://{_S3_BUCKET}.s3.amazonaws.com/{key}"
    if STORAGE_BACKEND == "gcs":
        key = f"{_GCS_PREFIX}/{relative_path}" if _GCS_PREFIX else relative_path
        return f"https://storage.googleapis.com/{_GCS_BUCKET}/{key}"
    return str(ARCHIVE_ROOT / relative_path)
