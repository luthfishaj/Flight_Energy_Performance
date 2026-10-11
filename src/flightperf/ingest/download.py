"""Downloading files and recording where they came from.

Every file used by the project gets downloaded through here, so that the
URL, the download date, the file size and a checksum all end up in the
dim_source table. Without that there is no way to prove, months later, which
version of a dataset produced a given number.

Files are saved into data/raw and never edited. If the processing turns out
to be wrong it can be fixed without downloading hundreds of megabytes again.
"""

import hashlib
import logging
from datetime import UTC, datetime

import requests
from tqdm import tqdm

log = logging.getLogger(__name__)

# Download in 1 MB pieces rather than loading the whole file into memory.
CHUNK_BYTES = 1024 * 1024


def download_file(url, target_path, force=False):
    """Download a URL to a file, skipping it if the file is already there.

    Returns the path. Pass force=True to download again anyway.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists() and not force:
        size_mb = target_path.stat().st_size / 1_000_000
        log.info("already downloaded: %s (%.1f MB)", target_path.name, size_mb)
        return target_path

    log.info("downloading %s", url)

    # stream=True so the file goes straight to disk in pieces.
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))

    # Write to a .part file first, then rename. That way an interrupted
    # download cannot leave a half-finished file that looks complete.
    part_path = target_path.with_suffix(target_path.suffix + ".part")

    with open(part_path, "wb") as f:
        bar = tqdm(total=total, unit="B", unit_scale=True, desc=target_path.name)
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            f.write(chunk)
            bar.update(len(chunk))
        bar.close()

    part_path.replace(target_path)
    log.info("saved %s", target_path)
    return target_path


def sha256_of(path):
    """Checksum of a file, so the exact version used can be identified later."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def record_source(con, source_id, source_name, publisher, url, licence,
                  file_path, rows_loaded, notes=None):
    """Write one row into dim_source describing a downloaded file.

    Uses ON CONFLICT so that re-running an ingest updates the existing record
    instead of failing on a duplicate key. An UPDATE rather than a
    DELETE-then-INSERT matters here, because fact_flight points at this row
    and deleting it would break the foreign key.
    """
    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.dim_source (
                source_id, source_name, publisher, source_url, licence,
                file_name, file_bytes, file_sha256, downloaded_at_utc,
                rows_loaded, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
                source_name       = EXCLUDED.source_name,
                publisher         = EXCLUDED.publisher,
                source_url        = EXCLUDED.source_url,
                licence           = EXCLUDED.licence,
                file_name         = EXCLUDED.file_name,
                file_bytes        = EXCLUDED.file_bytes,
                file_sha256       = EXCLUDED.file_sha256,
                downloaded_at_utc = EXCLUDED.downloaded_at_utc,
                rows_loaded       = EXCLUDED.rows_loaded,
                notes             = EXCLUDED.notes
            """,
            [
                source_id,
                source_name,
                publisher,
                url,
                licence,
                file_path.name,
                file_path.stat().st_size,
                sha256_of(file_path),
                datetime.now(UTC),
                rows_loaded,
                notes,
            ],
        )
    log.info("recorded source %s (%s rows)", source_id, format(rows_loaded, ","))
