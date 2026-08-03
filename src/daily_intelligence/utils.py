from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

TRACKING_QUERY_PREFIXES = ("utm_", "guce_", "guccounter", "ref", "source")
_ATOMIC_WRITE_LOCKS: WeakValueDictionary[str, Lock] = WeakValueDictionary()
_ATOMIC_WRITE_LOCKS_GUARD = Lock()


def environment_value(name: str) -> str | None:
    """处理：读取调用方声明的环境变量，不让工具层耦合具体秘密名称。
    输入：
    - ``name``：待读取、注册或解析的稳定名称。
    输出：封装“读取调用方声明的环境变量，
      不让工具层耦合具体秘密名称”业务结果的 ``str | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    return os.getenv(name)


def now_iso(timezone: str) -> str:
    """处理：返回配置时区中的秒级 ISO 当前时间。
    输入：
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：“返回配置时区中的秒级 ISO 当前时间”得到的规范字符串，供调用方存储、比较或展示。
    """
    return datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds")


def today_str(timezone: str) -> str:
    """处理：返回配置时区中的当前日期字符串。
    输入：
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：“返回配置时区中的当前日期字符串”得到的规范字符串，供调用方存储、比较或展示。
    """
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def timestamp_slug(timezone: str) -> str:
    """处理：生成适合文件名的配置时区时间戳。
    输入：
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：“生成适合文件名的配置时区时间戳”得到的规范字符串，供调用方存储、比较或展示。
    """
    return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d_%H-%M-%S")


def canonicalize_url(url: str) -> str:
    """处理：统一 HTTP(S) 协议、主机、路径和查询参数，并移除跟踪字段。
    输入：
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：经过选择、规范化或安全处理的 URL 字符串，供后续访问或渲染使用。
    """
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in {"http", "https"} else parts.scheme
    netloc = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_for_source_filter(url: str) -> str:
    """处理：移除跟踪参数，同时保留来源过滤规则使用的 URL 形状。
    输入：
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：经过选择、规范化或安全处理的 URL 字符串，供后续访问或渲染使用。
    """
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
    )


def item_id(source_id: str, canonical_url: str) -> str:
    """处理：根据来源 ID 和规范 URL 生成稳定条目 ID。
    输入：
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``canonical_url``：已去跟踪参数并规范主机和路径的 URL；参与稳定身份计算。
    输出：可跨修订关联的稳定字符串标识，供索引、状态或发布记录使用。
    """
    digest = hashlib.sha256(f"{source_id}|{canonical_url}".encode()).hexdigest()[:12]
    return f"{source_id}-{digest}"


def read_json(path: Path) -> dict[str, Any] | list[Any]:
    """处理：读取 UTF-8 JSON 文件并返回根级对象或数组。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：JSON 根值；保持文件中的对象或数组结构，不擅自改写字段与顺序。
    """
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_object(path: Path, label: str = "JSON document") -> dict[str, Any]:
    """处理：读取 JSON 文件并要求根值必须是对象。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``label``：用于错误消息的字段或产物名称，使失败能定位到具体输入。
    输出：“读取 JSON 文件并要求根值必须是对象”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _temporary_sibling(path: Path) -> Path:
    """处理：在目标文件同目录生成唯一临时文件路径。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：指向“在目标文件同目录生成唯一临时文件路径”所生成、定位或确认产物的本地路径。
    """
    return path.with_name(f".{path.name}.{uuid4().hex}.tmp")


def _atomic_write_lock(path: Path) -> Lock:
    """处理：获取目标路径专属的进程内写锁。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：封装“获取目标路径专属的进程内写锁”业务结果的 ``Lock`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    key = str(path.absolute())
    # 只在登记表操作期间持有全局锁；实际磁盘写入由目标路径自己的锁串行化。
    with _ATOMIC_WRITE_LOCKS_GUARD:
        return _ATOMIC_WRITE_LOCKS.setdefault(key, Lock())


def write_bytes_atomic(path: Path, content: bytes) -> Path:
    """处理：使用独立临时文件原子替换目标，避免并发写者共享临时名称。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``content``：待编码、解析或写入的原始内容；边界和可信级别由当前函数说明。
    输出：指向“使用独立临时文件原子替换目标，
      避免并发写者共享临时名称”所生成、定位或确认产物的本地路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    with _atomic_write_lock(path):
        try:
            # 先完整写入同目录的唯一临时文件，再原子替换，读者不会看到半份内容。
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            # 替换成功时临时文件已经消失；失败时也必须清理残留。
            temporary.unlink(missing_ok=True)
    return path


def write_text_atomic(path: Path, text: str) -> Path:
    """处理：把 UTF-8 文本通过同目录临时文件原子写入目标路径。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``text``：待解析、匹配或渲染的文本；作为不可信数据时会先转义或清理。
    输出：指向“把 UTF-8 文本通过同目录临时文件原子写入目标路径”所生成、定位或确认产物的本地路径
      。
    """
    return write_bytes_atomic(path, text.encode("utf-8"))


def write_json(path: Path, data: object) -> Path:
    """处理：把 Python 对象序列化为可读 UTF-8 JSON 并原子写入目标文件。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``data``：待持久化的 Python 对象；会序列化为 UTF-8 JSON，不执行其中内容。
    输出：指向“把 Python 对象序列化为可读 UTF-8 JSON 并原子写入目标文件”所生成、定位或确认产物的
      本地路径。
    """
    serialized = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return write_bytes_atomic(path, serialized)


def clean_title(value: str) -> str:
    """处理：折叠标题中的空白字符并去除首尾空白，保留原始文字与标点。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：“折叠标题中的空白字符并去除首尾空白，保留原始文字与标点”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    return " ".join((value or "").split()).strip()
