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
from PIL import Image, ImageStat, UnidentifiedImageError

from .config import MediaConfig
from .image_policy import is_placeholder_image_url, normalize_image_candidates
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
_IMAGE_CACHE_SCHEMA_VERSION = "1.1"


class ImageDownloadError(ValueError):
    """处理：表示图片 URL、网络响应、格式或安全校验失败。
    输入：
    - 无显式业务参数：不声明额外构造字段；该定义以 ``ValueError`` 为基础，
      通过类成员承担“表示图片 URL、网络响应、格式或安全校验失败”职责。
    输出：构造后的 ``ImageDownloadError`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    pass


@dataclass(frozen=True, slots=True)
class DownloadedImage:
    """处理：记录已验证本地图片的来源、哈希、尺寸和复用信息。
    输入：
    - ``source_url``：来源或图片的原始 URL；进入网络或索引前会执行相应规范化与安全检查。
    - ``resolved_url``：经过全部重定向并通过公网校验的最终图片 URL。
    - ``local_path``：图片在数据根下的内容寻址相对路径。
    - ``content_type``：HTTP 内容类型或待上传文件 MIME 类型；用于解析、校验和响应头。
    - ``sha256``：图片内容的 SHA-256；用于去重、校验和远程上传关联。
    - ``byte_size``：已验证图片文件的字节数。
    - ``width``：已验证栅格图片宽度，单位为像素。
    - ``height``：已验证栅格图片高度，单位为像素。
    - ``reused``：是否复用了已有且哈希一致的内容寻址文件。
    - ``etag``：服务端 ETag；用于下一次条件请求避免重复下载。
    - ``last_modified``：服务端 Last-Modified 值；用于下一次条件请求。
    输出：构造后的 ``DownloadedImage`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
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
    """处理：返回带 UTC 时区的当前时间。
    输入：
    - 无显式业务参数：不接收参数；读取系统 UTC 时钟并返回带时区时间。
    输出：封装“返回带 UTC 时区的当前时间”业务结果的 ``datetime`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    return datetime.now(UTC)


def _utc_iso(value: datetime | None = None) -> str:
    """处理：把 UTC 时间格式化为秒级 ISO 字符串。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：“把 UTC 时间格式化为秒级 ISO 字符串”得到的规范字符串，供调用方存储、比较或展示。
    """
    return (value or _utc_now()).isoformat(timespec="seconds")


def _parse_cache_time(value: object) -> datetime | None:
    """处理：把图片缓存中的 ISO 时间解析为 UTC 时间，非法值返回 None。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：封装“把图片缓存中的 ISO 时间解析为 UTC 时间，
      非法值返回 None”业务结果的 ``datetime | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _image_cache_path(data_dir: Path) -> Path:
    """处理：返回运行数据根中的图片下载缓存文件。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“返回运行数据根中的图片下载缓存文件”所生成、定位或确认产物的本地路径。
    """
    return data_dir / "media" / "image-cache.json"


def _load_image_cache(data_dir: Path) -> dict[str, Any]:
    """处理：读取版本化图片缓存并校验 entries 根对象，损坏时回退为空缓存。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：“读取版本化图片缓存并校验 entries 根对象，损坏时回退为空缓存”形成的结构化字典；
      典型键包括 entries、schema_version、updated_at。
    """
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
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _IMAGE_CACHE_SCHEMA_VERSION
        or not isinstance(payload.get("entries"), dict)
    ):
        payload = {}
    return {
        "schema_version": _IMAGE_CACHE_SCHEMA_VERSION,
        "updated_at": payload.get("updated_at"),
        "entries": dict(payload.get("entries", {})),
    }


def _write_image_cache(data_dir: Path, cache: dict[str, Any]) -> None:
    """处理：把内存中的图片成功与失败记录原子写入当前数据根的缓存文件。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``cache``：本地持久化缓存对象；包含状态、时间、响应元数据和可复用结果。
    输出：不返回新数据；完成“把内存中的图片成功与失败记录原子写入当前数据根的缓存文件”，
      副作用限于该处理声明的受控对象或产物。
    """
    cache["schema_version"] = _IMAGE_CACHE_SCHEMA_VERSION
    cache["updated_at"] = _utc_iso()
    write_json(_image_cache_path(data_dir), cache)


def _safe_cached_image_path(data_dir: Path, value: object) -> Path | None:
    """处理：把缓存相对路径限制在媒体目录并返回规范绝对路径。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：指向“把缓存相对路径限制在媒体目录并返回规范绝对路径”所生成、定位或确认产物的本地路径；
      条件不满足时返回 None。
    """
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
        # 缓存记录属于不可信状态，命中前仍要阻止绝对路径和目录穿越。
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
    """处理：校验缓存状态、时效、路径、大小和哈希后恢复图片结果。
    输入：
    - ``source_url``：来源或图片的原始 URL；进入网络或索引前会执行相应规范化与安全检查。
    - ``entry``：图片缓存中的单条记录；校验状态、时效、路径、大小和哈希后才可复用。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    输出：通过公网、大小、格式和像素校验的本地图片记录，供报告渲染和远程投影复用。
    """
    if not isinstance(entry, dict):
        return None
    status = str(entry.get("status") or "")
    checked_at = _parse_cache_time(entry.get("checked_at"))
    now = _utc_now()
    if status == "failed":
        retry_after = _parse_cache_time(entry.get("retry_after"))
        if retry_after and now < retry_after:
            # 短期负缓存抑制反复请求坏链接，但到期后必须允许重新验证。
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
    """处理：把成功下载结果转换为可持久化缓存记录。
    输入：
    - ``downloaded``：已通过安全和格式校验的图片结果；包含最终 URL、路径、哈希和尺寸。
    输出：“把成功下载结果转换为可持久化缓存记录”形成的结构化字典；
      典型键包括 byte_size、checked_at、content_type、etag、height、last_modified、local_path、r
      esolved_url、sha256、status、width。
    """
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
    """处理：把下载异常转换为带下次重试时间的负缓存记录。
    输入：
    - ``exc``：图片下载失败异常；提取错误文本并生成带重试时间的负缓存。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    输出：“把下载异常转换为带下次重试时间的负缓存记录”形成的结构化字典；
      典型键包括 checked_at、error、retry_after、status。
    """
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
    """处理：判断 IP 地址是否可由公网路由，排除本地和保留网段。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    address = ipaddress.ip_address(value)
    return address.is_global


def _resolve_system_addresses(hostname: str, port: int) -> tuple[str, ...]:
    """处理：通过系统解析器获取图片主机的全部连接地址。
    输入：
    - ``hostname``：图片 URL 的主机名；解析出的全部地址都必须可由公网路由。
    - ``port``：本地监控服务器监听端口；0 表示让操作系统分配可用端口。
    输出：“通过系统解析器获取图片主机的全部连接地址”得到的固定结构结果；
      各位置分别承载处理说明中的主结果和伴随状态。
    """
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
    """处理：判断 IPv4 地址是否属于代理软件使用的 fake-IP 网段。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    address = ipaddress.ip_address(value)
    return isinstance(address, ipaddress.IPv4Address) and address in _PROXY_FAKE_IP_NETWORK


@lru_cache(maxsize=512)
def _resolve_public_dns_addresses(hostname: str) -> tuple[str, ...]:
    """处理：通过可信公共 DNS 解析 fake-IP 主机名，失败时保持关闭。
    输入：
    - ``hostname``：图片 URL 的主机名；解析出的全部地址都必须可由公网路由。
    输出：“通过可信公共 DNS 解析 fake-IP 主机名，失败时保持关闭”得到的固定结构结果；
      各位置分别承载处理说明中的主结果和伴随状态。
    """
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
    """处理：拒绝凭证、非 HTTP 协议、异常端口和非公网图片主机。
    输入：
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：不返回新数据；完成“拒绝凭证、非 HTTP 协议、异常端口和非公网图片主机”，
      副作用限于该处理声明的受控对象或产物。
    """
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
    if is_placeholder_image_url(url):
        raise ImageDownloadError("image URL is a known placeholder")

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
        # 代理 fake-IP 不能证明目标公网属性，必须再经可信公共 DNS 确认。
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
    """处理：验证图片格式、像素上限和信息量，并返回规范元数据。
    输入：
    - ``content``：待编码、解析或写入的原始内容；边界和可信级别由当前函数说明。
    - ``max_pixels``：允许解码的最大总像素数；用于阻止解压炸弹和超大图片。
    输出：“验证图片格式、像素上限和信息量，并返回规范元数据”得到的固定结构结果；
      返回位置依次对应 content_type、extension、width、height。
    """
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
        with Image.open(BytesIO(content)) as image:
            sample = image.convert("RGB")
            sample.thumbnail((64, 64))
            pixel_count = sample.width * sample.height
            statistics = ImageStat.Stat(sample)
            colors = sample.getcolors(maxcolors=pixel_count)
            dominant_ratio = (
                max(count for count, _color in colors) / pixel_count
                if colors and pixel_count
                else 0.0
            )
            channel_spans = [
                maximum - minimum for minimum, maximum in sample.getextrema()
            ]
            if max(statistics.stddev, default=0.0) < 1.0 or (
                dominant_ratio >= 0.985 and max(channel_spans, default=0) <= 24
            ):
                raise ImageDownloadError(
                    "downloaded image is a low-information placeholder"
                )
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
    """处理：构建图片请求头，并拒绝可注入换行的 Referer。
    输入：
    - ``referer``：可选来源页面 URL；通过换行和协议检查后才写入请求头。
    输出：“构建图片请求头，并拒绝可注入换行的 Referer”形成的结构化字典；
      典型键包括 Accept、User-Agent。
    """
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
    """处理：返回适合 JSON Schema 与远程发布器的 ASCII URI。
    输入：
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：经过选择、规范化或安全处理的 URL 字符串，供后续访问或渲染使用。
    """
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
    """处理：把单张不可信公网栅格图片下载到内容寻址的本地存储。
    输入：
    - ``source_url``：报告索引提供的不可信公网图片 URL；每次重定向后都会重新执行公网校验。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``referer``：可选来源页面 URL；通过换行和协议检查后才写入请求头。
    - ``max_bytes``：允许读取或下载的最大字节数；达到上限后停止或报错。
    - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
    - ``url_validator``：每次初始请求和重定向前调用的 URL 安全校验器；失败时必须阻止下载。
    输出：已验证并保存到内容寻址目录的图片记录；
      包含最终 URL、本地相对路径、哈希、字节数、尺寸和复用状态。
    """
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
            # 每一次重定向后的 URL 都重新校验，防止公网入口跳转到内网地址。
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
    # 内容寻址使相同图片跨报告复用，同时避免用不可信文件名落盘。
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
    """处理：从全部报告栏目展开结构化简报列表。
    输入：
    - ``report``：当前报告结构；包含栏目、简报或事件、来源引用及质量元数据。
    输出：“从全部报告栏目展开结构化简报列表”得到的有序结构化记录；
      每项承载处理说明所定义的身份、证据或状态字段，可直接交给下一阶段。
    """
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
    """处理：使用共享连接池和同域限制下载一个优先级批次。
    输入：
    - ``rows``：同一优先级的图片候选二元组；每项依次提供公网图片 URL 和可选来源页 Referer。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``max_bytes``：允许读取或下载的最大字节数；达到上限后停止或报错。
    - ``downloader``：图片下载函数；接收候选 URL、来源页和安全预算，返回已验证的本地图片记录。
    输出：通过公网、大小、格式和像素校验的本地图片记录，供报告渲染和远程投影复用。
    """
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
        """处理：在并发限制内执行单项任务。
        输入：
        - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
        - ``source_url``：来源或图片的原始 URL；进入网络或索引前会执行相应规范化与安全检查。
        - ``referer``：可选来源页面 URL；通过换行和协议检查后才写入请求头。
        输出：通过公网、大小、格式和像素校验的本地图片记录，供报告渲染和远程投影复用。
        """
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


def _indexed_image_candidates(indexed: dict[str, Any]) -> list[str]:
    """处理：合并索引主图片与元数据候选并规范化 URL。
    输入：
    - ``indexed``：权威来源索引中与当前 item_id 对应的条目记录。
    输出：“合并索引主图片与元数据候选并规范化 URL”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    values: list[object] = [indexed.get("image_url")]
    direct_candidates = indexed.get("image_candidates")
    if isinstance(direct_candidates, list):
        values.extend(direct_candidates)
    metadata = indexed.get("metadata")
    if isinstance(metadata, dict):
        metadata_candidates = metadata.get("image_candidates")
        if isinstance(metadata_candidates, list):
            values.extend(metadata_candidates)
    return normalize_image_candidates(values)


def materialize_report_images(
    report: dict[str, Any],
    index: dict[str, Any],
    data_dir: Path,
    config: MediaConfig,
    *,
    downloader: Callable[..., DownloadedImage] = download_image,
) -> list[str]:
    """处理：将权威索引图片绑定到报告简报并持久化本地副本。
    输入：
    - ``report``：已编译报告对象；函数只为与权威索引匹配的简报附加本地图片元数据。
    - ``index``：权威来源索引；提供 item_id 对应的来源页、图片候选和来源身份。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``downloader``：可注入的图片下载函数；生产使用安全下载器，测试可替换为确定性实现。
    输出：未能附图或降级时的警告列表；report 会就地获得通过预算和安全校验的本地图片元数据。
    """
    briefs = _report_briefs(report)
    for brief in briefs:
        brief.pop("image", None)
    indexed_items = {
        str(item.get("item_id")): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    candidates: list[
        tuple[int, dict[str, Any], dict[str, Any], list[str]]
    ] = []
    for order, brief in enumerate(briefs):
        indexed = indexed_items.get(str(brief.get("item_id")))
        if not indexed:
            continue
        image_candidates = _indexed_image_candidates(indexed)
        if not image_candidates:
            continue
        candidates.append((order, brief, indexed, image_candidates))
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

    def resolve_rows(
        rows: list[tuple[str, str | None]],
        max_bytes: int,
    ) -> None:
        """处理：复用图片缓存并批量下载尚未解析的候选 URL，随后写入检查点。
        输入：
        - ``rows``：当前优先级的图片候选二元组；每项是 image_url 与可选 referer，
          URL 会先查缓存再下载。
        - ``max_bytes``：允许读取或下载的最大字节数；达到上限后停止或报错。
        输出：不返回新数据；完成“复用图片缓存并批量下载尚未解析的候选 URL，随后写入检查点”，
          副作用限于该处理声明的受控对象或产物。
        """
        nonlocal cache_warning_emitted
        batch: list[tuple[str, str | None]] = []
        batch_urls: set[str] = set()
        for image_url, referer in rows:
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
                    # 成功缓存和仍在退避期的失败缓存都视为已解析，避免重复网络工作。
                    resolved_by_url[image_url] = cached
                    continue
            batch.append((image_url, referer))
            batch_urls.add(image_url)
        if not batch:
            return

        batch_results = _download_image_batch(
            batch,
            data_dir,
            config,
            max_bytes,
            downloader,
        )
        resolved_by_url.update(batch_results)
        if not cache_enabled:
            return
        for image_url, result in batch_results.items():
            cache_key = _serialized_http_url(image_url)
            cache_entries[cache_key] = (
                _cache_success(result)
                if isinstance(result, DownloadedImage)
                else _cache_failure(result, config)
            )
        try:
            # 每个批次后落盘检查点；进程中断也能复用已经完成的下载。
            _write_image_cache(data_dir, cache)
        except OSError as exc:
            if not cache_warning_emitted:
                warnings.append(
                    "image cache checkpoint failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                cache_warning_emitted = True

    cursor = 0
    while cursor < len(candidates):
        if int(metrics["attached"]) >= config.max_images_per_report:
            metrics["skipped_budget"] += len(candidates) - cursor
            break
        remaining_bytes = config.max_total_bytes - int(metrics["total_bytes"])
        if remaining_bytes <= 0:
            metrics["skipped_budget"] += len(candidates) - cursor
            break

        batch_limit = config.global_concurrency if cache_enabled else 1
        primary_rows = [
            (
                image_candidates[0],
                str(indexed.get("url") or "") or None,
            )
            for _order, _brief, indexed, image_candidates
            in candidates[cursor : cursor + batch_limit]
        ]
        resolve_rows(
            primary_rows,
            min(config.max_image_bytes, remaining_bytes),
        )

        _order, brief, indexed, image_candidates = candidates[cursor]
        cursor += 1
        item_id = str(brief.get("item_id") or "")
        downloaded: DownloadedImage | None = None
        failures: list[Exception] = []
        referer = str(indexed.get("url") or "") or None
        for image_url in image_candidates:
            if image_url not in resolved_by_url:
                remaining_bytes = config.max_total_bytes - int(metrics["total_bytes"])
                resolve_rows(
                    [(image_url, referer)],
                    min(config.max_image_bytes, remaining_bytes),
                )
            result = resolved_by_url.get(image_url)
            if isinstance(result, DownloadedImage):
                downloaded = result
                break
            failures.append(
                result
                if isinstance(result, Exception)
                else ImageDownloadError("image download did not produce a result")
            )
        if downloaded is None:
            exc = failures[-1]
            metrics["failed"] += 1
            warnings.append(
                f"image omitted for item_id={item_id!r} after "
                f"{len(failures)} candidate(s): {type(exc).__name__}: {exc}"
            )
            continue

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
    """处理：为确定性报告候选预热图片缓存，但不执行发布。
    输入：
    - ``index``：当前来源索引对象；包含规范条目、来源结果、策略和采集时间。
    - ``item_ids``：待处理条目的稳定 ID 集合；用于限定预算和授权范围。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    输出：“为确定性报告候选预热图片缓存，但不执行发布”形成的结构化字典；
      典型键包括 briefs、elapsed_seconds、id、importance、item_id、name、primary_source、request
      ed_item_count、sections、source_rank、title、url。
    """
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
