"""Validate the repository's Markdown map, translations, and local links."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".agents",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "output",
    "tmp",
}
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
ENTRY_DOCUMENTS = (ROOT / "AGENTS.md", ROOT / "ARCHITECTURE.md")
MAX_VERIFICATION_AGE_DAYS = 180
VERIFIED_PATTERN = re.compile(r"\*\*Last verified:\*\* (\d{4}-\d{2}-\d{2})")


def canonical_records() -> list[Path]:
    """处理：汇总根入口和 docs 下需要中文译文的英文权威记录。
    输入：
    - 无显式业务参数：不接收参数；扫描仓库根入口及 docs 目录，并排除 zh-CN、生成和历史记录。
    输出：需要维护中文译文的英文 Markdown 路径列表；
      包含根级权威文档与 docs 中非生成、非历史记录。
    """
    records = [*ENTRY_DOCUMENTS]
    records.extend(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if "zh-CN" not in path.parts
    )
    return sorted(records)


def translation_path(record: Path) -> Path:
    """处理：把英文权威记录映射到 docs/zh-CN 中的译文路径。
    输入：
    - ``record``：docs 目录中的英文权威 Markdown 路径；用于计算对应中文译文位置。
    输出：指向“把英文权威记录映射到 docs/zh-CN 中的译文路径”所生成、定位或确认产物的本地路径。
    """
    if record.parent == ROOT:
        return ROOT / "docs" / "zh-CN" / record.name
    return ROOT / "docs" / "zh-CN" / record.relative_to(ROOT / "docs")


def markdown_files() -> list[Path]:
    """处理：列出文档检查范围内的全部 Markdown 文件。
    输入：
    - 无显式业务参数：不接收参数；扫描仓库根入口、docs 和中文译文中的 Markdown 文件。
    输出：文档链接检查要扫描的 Markdown 路径列表；覆盖根入口、英文记录及 docs/zh-CN 译文。
    """
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not (set(path.relative_to(ROOT).parts) & IGNORED_PARTS)
    )


def _local_target(document: Path, raw_target: str) -> Path | None:
    """处理：把 Markdown 本地链接解析为可检查的绝对路径。
    输入：
    - ``document``：当前正在检查链接的 Markdown 文件路径；相对链接以其父目录为基准解析。
    - ``raw_target``：Markdown 链接中尚未解码的目标文本；锚点和外部 URL 会被区分处理。
    输出：指向“把 Markdown 本地链接解析为可检查的绝对路径”所生成、定位或确认产物的本地路径；
      条件不满足时返回 None。
    """
    target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
    if not target or target.startswith("#") or "://" in target or target.startswith("mailto:"):
        return None
    relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not relative:
        return None
    return (document.parent / relative).resolve()


def validate_docs() -> list[str]:
    """处理：检查 AGENTS 大小、记录元数据、中英文配对和本地链接。
    输入：
    - 无显式业务参数：不接收参数；读取文档目录、英中映射和本地链接，汇总缺失或失效记录。
    输出：可操作的校验错误消息列表；空列表表示通过当前规则。
    """
    errors: list[str] = []
    agent_lines = len((ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines())
    if agent_lines > 110:
        errors.append(f"AGENTS.md has {agent_lines} lines; keep the map at or below 110")

    for record in canonical_records():
        if not record.is_file():
            errors.append(f"Missing canonical record: {record.relative_to(ROOT)}")
            continue
        translation = translation_path(record)
        if not translation.is_file():
            errors.append(
                "Missing Chinese translation: "
                f"{translation.relative_to(ROOT)} for {record.relative_to(ROOT)}"
            )
        if record.name == "AGENTS.md":
            continue
        text = record.read_text(encoding="utf-8")
        if "**Status:**" not in text:
            errors.append(f"Missing status: {record.relative_to(ROOT)}")
        if "**Owner:**" not in text:
            errors.append(f"Missing owner: {record.relative_to(ROOT)}")
        match = VERIFIED_PATTERN.search(text)
        if not match:
            errors.append(f"Missing verification date: {record.relative_to(ROOT)}")
            continue
        verified = date.fromisoformat(match.group(1))
        age = (date.today() - verified).days
        if age < 0 or age > MAX_VERIFICATION_AGE_DAYS:
            errors.append(
                f"Stale verification date in {record.relative_to(ROOT)}: "
                f"{verified} ({age} days old)"
            )

    for document in markdown_files():
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = _local_target(document, raw_target)
            if target is not None and not target.exists():
                errors.append(
                    f"Broken local link in {document.relative_to(ROOT)}: {raw_target}"
                )
    return errors


def main() -> int:
    """处理：解析命令行参数并执行对应入口。
    输入：
    - 无显式业务参数：不接收参数；执行完整文档目录、翻译和链接检查并打印结果。
    输出：进程退出码；0 表示检查通过，非 0 表示存在已输出的错误。
    """
    errors = validate_docs()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        f"Documentation checks passed: {len(canonical_records())} canonical records, "
        f"{len(markdown_files())} Markdown files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
