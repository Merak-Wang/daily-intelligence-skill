from __future__ import annotations

import hashlib
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from .config import MediaConfig
from .storage import write_bytes_atomic
from .utils import read_json, write_json

_ALLOWED_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_USER_AGENT = "DailyIntelligenceMedia/1.0 (+public-news-image-fetcher)"
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_PUBLIC_DNS_ENDPOINT = "https://dns.google/resolve"
_IMAGE_CACHE_SCHEMA_VERSION = "1.0"


class ImageDownloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadedImage:
    source_url: str
    resolved_url: str
    local_path: str
    content_type: str
    sha256: str
    byte_size: int
    width: int
    height: int
    reused: bool
    etag: str | None = None
    last_modified: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds")


def _parse_cache_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _image_cache_path(data_dir: Path) -> Path:
    return data_dir / "media" / "image-cache.json"


def _load_image_cache(data_dir: Path) -> dict[str, Any]:
    path = _image_cache_path(data_dir)
    if not path.exists():
        return {
            "schema_version": _IMAGE_CACHE_SCHEMA_VERSION,
            "updated_at": None,
            "entries": {},
        }
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        payload = {}
    return {
        "schema_version": _IMAGE_CACHE_SCHEMA_VERSION,
        "updated_at": payload.get("updated_at"),
        "entries": dict(payload.get("entries", {})),
    }


def _write_image_cache(data_dir: Path, cache: dict[str, Any]) -> None:
    cache["schema_version"] = _IMAGE_CACHE_SCHEMA_VERSION
    cache["updated_at"] = _utc_iso()
    write_json(_image_cache_path(data_dir), cache)


def _safe_cached_image_path(data_dir: Path, value: object) -> Path | None:
    relative = Path(str(value or "").replace("\\", "/"))
    if (
        not relative.parts
        or relative.is_absolute()
        or relative.parts[0] != "media"
        or ".." in relative.parts
    ):
        return None
    root = (data_dir / "media").resolve()
    candidate = (data_dir / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _cached_download(
    source_url: str,
    entry: object,
    data_dir: Path,
    config: MediaConfig,
) -> DownloadedImage | ImageDownloadError | None:
    if not isinstance(entry, dict):
        return None
    status = str(entry.get("status") or "")
    checked_at = _parse_cache_time(entry.get("checked_at"))
    now = _utc_now()
    if status == "failed":
        retry_after = _parse_cache_time(entry.get("retry_after"))
        if retry_after and now < retry_after:
            return ImageDownloadError(
                "cached image failure; retry after " + retry_after.isoformat(timespec="seconds")
            )
        return None
    if status != "success" or checked_at is None:
        return None
    if now - checked_at > timedelta(hours=config.cache_success_ttl_hours):
        return None
    path = _safe_cached_image_path(data_dir, entry.get("local_path"))
    if path is None or not path.is_file():
        return None
    try:
        byte_size = int(entry["byte_size"])
        digest = str(entry["sha256"])
        if path.stat().st_size != byte_size:
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return None
        return DownloadedImage(
            source_url=_serialized_http_url(source_url),
            resolved_url=_serialized_http_url(entry.get("resolved_url") or source_url),
            local_path=str(entry["local_path"]),
            content_type=str(entry["content_type"]),
            sha256=digest,
            byte_size=byte_size,
            width=int(entry["width"]),
            height=int(entry["height"]),
            reused=True,
            etag=str(entry.get("etag") or "") or None,
            last_modified=str(entry.get("last_modified") or "") or None,
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _cache_success(downloaded: DownloadedImage) -> dict[str, Any]:
    return {
        "status": "success",
        "checked_at": _utc_iso(),
        "resolved_url": downloaded.resolved_url,
        "local_path": downloaded.local_path,
        "content_type": downloaded.content_type,
        "sha256": downloaded.sha256,
        "byte_size": downloaded.byte_size,
        "width": downloaded.width,
        "height": downloaded.height,
        "etag": downloaded.etag,
        "last_modified": downloaded.last_modified,
    }


def _cache_failure(exc: Exception, config: MediaConfig) -> dict[str, Any]:
    now = _utc_now()
    return {
        "status": "failed",
        "checked_at": _utc_iso(now),
        "retry_after": _utc_iso(
            now + timedelta(minutes=config.cache_failure_retry_minutes)
        ),
        "error": f"{type(exc).__name__}: {exc}",
    }


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global


def _resolve_system_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        return tuple(
            sorted(
                {
                    str(result[4][0])
                    for result in socket.getaddrinfo(
                        hostname,
                        port,
                        type=socket.SOCK_STREAM,
                    )
                }
            )
        )
    except OSError as exc:
        raise ImageDownloadError(
            f"image host {hostname!r} could not be resolved"
        ) from exc


def _is_proxy_fake_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return isinstance(address, ipaddress.IPv4Address) and address in _PROXY_FAKE_IP_NETWORK


@lru_cache(maxsize=512)
def _resolve_public_dns_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve a fake-IP hostname through authenticated public DNS, failing closed."""
    try:
        with httpx.Client(
            timeout=5.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for record_type, answer_type in (("A", 1), ("AAAA", 28)):
                response = client.get(
                    _PUBLIC_DNS_ENDPOINT,
                    params={"name": hostname, "type": record_type},
                    headers={
                        "Accept": "application/dns-json",
                        "User-Agent": _USER_AGENT,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("Status") != 0:
                    continue
                addresses: set[str] = set()
                for answer in payload.get("Answer", []):
                    if not isinstance(answer, dict) or answer.get("type") != answer_type:
                        continue
                    value = str(answer.get("data") or "")
                    try:
                        ipaddress.ip_address(value)
                    except ValueError:
                        continue
                    addresses.add(value)
                if addresses:
                    return tuple(sorted(addresses))
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise ImageDownloadError(
            f"public DNS confirmation failed for image host {hostname!r}: "
            f"{type(exc).__name__}"
        ) from exc
    return ()


def assert_public_image_url(url: str) -> None:
    """Reject credentials, non-HTTP schemes, unusual ports, and non-public hosts."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageDownloadError("image URL must use public HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ImageDownloadError("image URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ImageDownloadError("image URL contains an invalid port") from exc
    if port not in {None, 80, 443}:
        raise ImageDownloadError("image URL must use port 80 or 443")

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ImageDownloadError("image URL host is not public")
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise ImageDownloadError("image URL resolved to a non-public network address")
        return

    addresses = _resolve_system_addresses(
        hostname,
        port or (443 if parsed.scheme == "https" else 80),
    )
    if addresses and all(_is_public_address(address) for address in addresses):
        return
    if addresses and all(_is_proxy_fake_address(address) for address in addresses):
        public_addresses = _resolve_public_dns_addresses(hostname)
        if public_addresses and all(
            _is_public_address(address) for address in public_addresses
        ):
            return
        raise ImageDownloadError(
            "image hostname uses proxy fake-IP DNS but its public destination "
            "could not be confirmed"
        )
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ImageDownloadError("image URL resolved to a non-public network address")


def _inspect_raster(content: bytes, max_pixels: int) -> tuple[str, str, int, int]:
    try:
        with Image.open(BytesIO(content)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if image_format not in _ALLOWED_FORMATS:
                raise ImageDownloadError(
                    "image format must be JPEG, PNG, GIF, or WebP"
                )
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageDownloadError(
                    f"image dimensions exceed the {max_pixels:,}-pixel safety limit"
                )
            image.verify()
    except ImageDownloadError:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as exc:
        raise ImageDownloadError("downloaded content is not a valid raster image") from exc
    content_type, extension = _ALLOWED_FORMATS[image_format]
    return content_type, extension, width, height


def _request_headers(referer: str | None) -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "image/jpeg,image/png,image/gif,image/webp;q=0.9",
    }
    if (
        referer
        and "\r" not in referer
        and "\n" not in referer
        and urlsplit(referer).scheme in {"http", "https"}
    ):
        headers["Referer"] = referer
    return headers


def _serialized_http_url(url: str) -> str:
    """Return an ASCII URI suitable for JSON Schema and remote publishers."""
    try:
        return str(httpx.URL(url))
    except (httpx.InvalidURL, TypeError) as exc:
        raise ImageDownloadError("image URL could not be serialized as a URI") from exc


def download_image(
    source_url: str,
    data_dir: Path,
    config: MediaConfig,
    *,
    referer: str | None = None,
    max_bytes: int | None = None,
    client: httpx.Client | None = None,
    url_validator: Callable[[str], None] = assert_public_image_url,
) -> DownloadedImage:
    """Download one untrusted public raster image into content-addressed local storage."""
    byte_limit = min(config.max_image_bytes, max_bytes or config.max_image_bytes)
    if byte_limit <= 0:
        raise ImageDownloadError("report image byte budget is exhausted")

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
    current_url = source_url
    deadline = time.monotonic() + config.request_timeout_seconds
    etag = None
    last_modified = None
    try:
        for redirect_count in range(config.max_redirects + 1):
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise ImageDownloadError("image request exceeded its total timeout")
            url_validator(current_url)
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise ImageDownloadError("image request exceeded its total timeout")
            host = urlsplit(current_url).hostname or "unknown"
            try:
                with client.stream(
                    "GET",
                    current_url,
                    headers=_request_headers(referer),
                    timeout=remaining_seconds,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise ImageDownloadError(
                                f"image host {host!r} returned a redirect without a location"
                            )
                        if redirect_count >= config.max_redirects:
                            raise ImageDownloadError("image redirect limit was exceeded")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 400:
                        raise ImageDownloadError(
                            f"image host {host!r} returned HTTP {response.status_code}"
                        )
                    etag = response.headers.get("etag")
                    last_modified = response.headers.get("last-modified")
                    raw_length = response.headers.get("content-length")
                    if raw_length:
                        try:
                            content_length = int(raw_length)
                        except ValueError:
                            content_length = None
                        if content_length is not None and content_length > byte_limit:
                            raise ImageDownloadError(
                                f"image exceeds the {byte_limit:,}-byte limit"
                            )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise ImageDownloadError(
                                "image request exceeded its total timeout"
                            )
                        total += len(chunk)
                        if total > byte_limit:
                            raise ImageDownloadError(
                                f"image exceeds the {byte_limit:,}-byte limit"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    break
            except ImageDownloadError:
                raise
            except httpx.HTTPError as exc:
                raise ImageDownloadError(
                    f"image request to host {host!r} failed: {type(exc).__name__}"
                ) from exc
        else:  # pragma: no cover - loop either breaks or raises
            raise ImageDownloadError("image redirect limit was exceeded")
    finally:
        if owns_client:
            client.close()

    if not content:
        raise ImageDownloadError("image response was empty")
    content_type, extension, width, height = _inspect_raster(
        content, config.max_image_pixels
    )
    digest = hashlib.sha256(content).hexdigest()
    relative_path = Path("media") / "images" / digest[:2] / f"{digest}{extension}"
    output_path = data_dir / relative_path
    reused = (
        output_path.is_file()
        and output_path.stat().st_size == len(content)
        and hashlib.sha256(output_path.read_bytes()).hexdigest() == digest
    )
    if not reused:
        write_bytes_atomic(output_path, content)
    return DownloadedImage(
        source_url=_serialized_http_url(source_url),
        resolved_url=_serialized_http_url(current_url),
        local_path=relative_path.as_posix(),
        content_type=content_type,
        sha256=digest,
        byte_size=len(content),
        width=width,
        height=height,
        reused=reused,
        etag=etag,
        last_modified=last_modified,
    )


def _report_briefs(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        brief
        for section in report.get("sections", [])
        if isinstance(section, dict)
        for brief in section.get("briefs", [])
        if isinstance(brief, dict)
    ]


def _download_image_batch(
    rows: list[tuple[str, str | None]],
    data_dir: Path,
    config: MediaConfig,
    max_bytes: int,
    downloader: Callable[..., DownloadedImage],
) -> dict[str, DownloadedImage | Exception]:
    """Download one priority batch with a shared connection pool and domain limits."""
    if not rows:
        return {}
    if downloader is not download_image:
        results: dict[str, DownloadedImage | Exception] = {}
        for source_url, referer in rows:
            try:
                results[source_url] = downloader(
                    source_url,
                    data_dir,
                    config,
                    referer=referer,
                    max_bytes=max_bytes,
                )
            except (ImageDownloadError, OSError) as exc:
                results[source_url] = exc
        return results

    domain_limits: dict[str, threading.BoundedSemaphore] = {}
    domain_lock = threading.Lock()

    def guarded(
        client: httpx.Client,
        source_url: str,
        referer: str | None,
    ) -> DownloadedImage:
        domain = (urlsplit(source_url).hostname or "unknown").casefold()
        with domain_lock:
            semaphore = domain_limits.setdefault(
                domain,
                threading.BoundedSemaphore(config.per_domain_concurrency),
            )
        with semaphore:
            return downloader(
                source_url,
                data_dir,
                config,
                referer=referer,
                max_bytes=max_bytes,
                client=client,
            )

    results = {}
    worker_count = min(config.global_concurrency, len(rows))
    with (
        httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client,
        ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="daily-intel-image",
        ) as executor,
    ):
        futures = {
            executor.submit(guarded, client, source_url, referer): source_url
            for source_url, referer in rows
        }
        for future in as_completed(futures):
            source_url = futures[future]
            try:
                results[source_url] = future.result()
            except (ImageDownloadError, OSError) as exc:
                results[source_url] = exc
    return results


def materialize_report_images(
    report: dict[str, Any],
    index: dict[str, Any],
    data_dir: Path,
    config: MediaConfig,
    *,
    downloader: Callable[..., DownloadedImage] = download_image,
) -> list[str]:
    """Bind authoritative index images to report briefs and persist local copies."""
    briefs = _report_briefs(report)
    for brief in briefs:
        brief.pop("image", None)
    indexed_items = {
        str(item.get("item_id")): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for order, brief in enumerate(briefs):
        indexed = indexed_items.get(str(brief.get("item_id")))
        if not indexed or not isinstance(indexed.get("image_url"), str):
            continue
        image_url = str(indexed["image_url"]).strip()
        if not image_url:
            continue
        candidates.append((order, brief, indexed))
    candidates.sort(
        key=lambda row: (
            -int(row[1].get("importance", 0)),
            int(row[1].get("source_rank", 1_000_000)),
            row[0],
        )
    )

    metrics = {
        "candidates": len(candidates),
        "attached": 0,
        "unique_files": 0,
        "reused_files": 0,
        "failed": 0,
        "skipped_budget": 0,
        "total_bytes": 0,
    }
    report["media_metrics"] = metrics
    if not config.enabled or config.max_images_per_report == 0:
        metrics["skipped_budget"] = len(candidates)
        return []

    warnings: list[str] = []
    unique_digests: set[str] = set()
    resolved_by_url: dict[str, DownloadedImage | Exception] = {}
    cache_enabled = downloader is download_image
    cache = _load_image_cache(data_dir) if cache_enabled else {"entries": {}}
    cache_entries = cache["entries"]
    cache_warning_emitted = False
    cursor = 0
    while cursor < len(candidates):
        if int(metrics["attached"]) >= config.max_images_per_report:
            metrics["skipped_budget"] += len(candidates) - cursor
            break
        remaining_bytes = config.max_total_bytes - int(metrics["total_bytes"])
        if remaining_bytes <= 0:
            metrics["skipped_budget"] += len(candidates) - cursor
            break

        current_url = str(candidates[cursor][2]["image_url"]).strip()
        if current_url not in resolved_by_url:
            batch_limit = config.global_concurrency if cache_enabled else 1
            batch: list[tuple[str, str | None]] = []
            batch_urls: set[str] = set()
            for _order, _brief, indexed in candidates[cursor:]:
                image_url = str(indexed["image_url"]).strip()
                if image_url in resolved_by_url or image_url in batch_urls:
                    continue
                if cache_enabled:
                    try:
                        cache_key = _serialized_http_url(image_url)
                    except ImageDownloadError as exc:
                        resolved_by_url[image_url] = exc
                        continue
                    cached = _cached_download(
                        image_url,
                        cache_entries.get(cache_key),
                        data_dir,
                        config,
                    )
                    if cached is not None:
                        resolved_by_url[image_url] = cached
                        continue
                batch.append(
                    (image_url, str(indexed.get("url") or "") or None)
                )
                batch_urls.add(image_url)
                if len(batch) >= batch_limit:
                    break

            if batch:
                batch_results = _download_image_batch(
                    batch,
                    data_dir,
                    config,
                    min(config.max_image_bytes, remaining_bytes),
                    downloader,
                )
                resolved_by_url.update(batch_results)
                if cache_enabled:
                    for image_url, result in batch_results.items():
                        cache_key = _serialized_http_url(image_url)
                        cache_entries[cache_key] = (
                            _cache_success(result)
                            if isinstance(result, DownloadedImage)
                            else _cache_failure(result, config)
                        )
                    try:
                        _write_image_cache(data_dir, cache)
                    except OSError as exc:
                        if not cache_warning_emitted:
                            warnings.append(
                                "image cache checkpoint failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                            cache_warning_emitted = True

        _order, brief, indexed = candidates[cursor]
        cursor += 1
        item_id = str(brief.get("item_id") or "")
        image_url = str(indexed["image_url"]).strip()
        result = resolved_by_url.get(image_url)
        if not isinstance(result, DownloadedImage):
            exc = result or ImageDownloadError("image download did not produce a result")
            metrics["failed"] += 1
            warnings.append(
                f"image omitted for item_id={item_id!r}: {type(exc).__name__}: {exc}"
            )
            continue
        downloaded = result

        if downloaded.sha256 not in unique_digests:
            remaining_bytes = config.max_total_bytes - int(metrics["total_bytes"])
            if downloaded.byte_size > remaining_bytes:
                metrics["skipped_budget"] += 1
                continue
            unique_digests.add(downloaded.sha256)
            metrics["unique_files"] += 1
            metrics["total_bytes"] += downloaded.byte_size
            if downloaded.reused:
                metrics["reused_files"] += 1
        source = brief.get("primary_source")
        credit = (
            str(source.get("name"))
            if isinstance(source, dict) and source.get("name")
            else str(indexed.get("source_name") or indexed.get("source_id") or "原始来源")
        )
        brief["image"] = {
            "source_url": downloaded.source_url,
            "resolved_url": downloaded.resolved_url,
            "local_path": downloaded.local_path,
            "content_type": downloaded.content_type,
            "sha256": downloaded.sha256,
            "byte_size": downloaded.byte_size,
            "width": downloaded.width,
            "height": downloaded.height,
            "caption": str(indexed.get("title") or brief.get("title") or "新闻配图"),
            "credit": credit,
        }
        metrics["attached"] += 1
    return warnings


def prefetch_index_images(
    index: dict[str, Any],
    item_ids: list[str],
    data_dir: Path,
    config: MediaConfig,
) -> dict[str, Any]:
    """Warm the image cache for deterministic report candidates without publishing."""
    indexed_items = {
        str(item.get("item_id")): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    briefs = []
    for rank, item_id in enumerate(dict.fromkeys(map(str, item_ids)), start=1):
        indexed = indexed_items.get(item_id)
        if not indexed:
            continue
        briefs.append(
            {
                "item_id": item_id,
                "importance": max(0, 100 - rank),
                "source_rank": rank,
                "title": indexed.get("title"),
                "primary_source": {
                    "id": indexed.get("source_id"),
                    "name": indexed.get("source_name"),
                    "url": indexed.get("url"),
                },
            }
        )
    scratch_report: dict[str, Any] = {
        "sections": [{"id": "prefetch", "briefs": briefs}]
    }
    started = time.perf_counter()
    warnings = materialize_report_images(
        scratch_report,
        index,
        data_dir,
        config,
    )
    return {
        **scratch_report.get("media_metrics", {}),
        "requested_item_count": len(item_ids),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "warnings": warnings,
    }
