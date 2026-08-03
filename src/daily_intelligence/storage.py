from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .utils import write_bytes_atomic as write_bytes_atomic
from .utils import write_text_atomic as write_text_atomic

_REVISION_RE = re.compile(r"-r(\d+)\.json$")


def next_revision(directory: Path, stem: str) -> int:
    """处理：扫描同名不可变产物并返回下一个修订号。
    输入：
    - ``directory``：包含同类版本化产物的目录；用于扫描或写入记录。
    - ``stem``：版本化产物文件名前缀；用于扫描现有修订号。
    输出：下一个可用的正整数修订号；用于创建不覆盖历史的版本化产物。
    """
    revisions: list[int] = []
    if directory.exists():
        for path in directory.glob(f"{stem}-r*.json"):
            match = _REVISION_RE.search(path.name)
            if match:
                revisions.append(int(match.group(1)))
    return max(revisions, default=0) + 1


def write_immutable_json(path: Path, data: object) -> Path:
    """处理：无覆盖竞态地创建完整且不可变的 JSON artifact。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``data``：待持久化的 Python 对象；会序列化为 UTF-8 JSON，不执行其中内容。
    输出：指向“无覆盖竞态地创建完整且不可变的 JSON artifact”所生成、定位或确认产物的本地路径。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            # 硬链接创建具有“目标必须不存在”语义，避免先检查后写入的竞态窗口。
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Refusing to overwrite immutable artifact: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


@contextmanager
def exclusive_lock(path: Path, payload: dict) -> Iterator[None]:
    """处理：以排他创建方式持有运行锁，并在上下文退出时释放。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
    输出：上下文管理器控制权；进入时资源已取得，退出时无论成功失败都会释放。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ``x`` 模式把“检查锁”和“创建锁”合为一次原子文件系统操作。
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Another run holds {path}. Remove it only after confirming no run is active."
        ) from exc
    try:
        yield
    finally:
        # 无论业务逻辑成功还是抛错，都释放仅属于本上下文的锁文件。
        path.unlink(missing_ok=True)
