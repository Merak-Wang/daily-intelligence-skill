"""Enforce concise Chinese input/output/logic notes on maintained Python definitions."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
MAINTENANCE_SCRIPTS = (
    "build_hermes_skill.py",
    "check_code_comments.py",
    "check_docs.py",
)
EMPTY_DESCRIPTION_PATTERNS = (
    "类型的处理结果",
    "元素结构见调用处",
    "参与“",
    "上述处理通过更新",
    "数据来自当前实例、外层受控作用域",
    "身份、状态、指标或映射关系",
    "有序领域对象列表",
    "每项只包含下游契约需要的字段",
    "主结果与伴随状态的固定结构数据",
    "领域对象；供调用方进入下一业务步骤",
    "的核心流程",
    "的内部处理步骤",
    "的单项处理",
    "当前步骤所需的",
    "对应的文本或标识",
    "默认语义由函数签名",
    "具体结构由",
    "描述上述处理状态",
)


def python_targets(root: Path = ROOT) -> list[Path]:
    """处理：列出规范实现和仓库维护脚本，不检查生成或安装快照。
    输入：
    - ``root``：安全边界或检查根目录；目标路径必须位于其中。
    输出：需要接受中文注释门禁的源码 Path 列表；包含规范包模块和三份维护脚本，
      不含测试与发布快照。
    """
    source_files = sorted((root / "src" / "daily_intelligence").glob("*.py"))
    script_files = [root / "scripts" / name for name in MAINTENANCE_SCRIPTS]
    return [*source_files, *script_files]


def definition_errors(path: Path, root: Path = ROOT) -> list[str]:
    """处理：检查一个 Python 文件内所有函数和类的中文结构化说明。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``root``：安全边界或检查根目录；目标路径必须位于其中。
    输出：可操作的校验错误消息列表；空列表表示通过当前规则。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[str] = []
    relative = path.relative_to(root)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        docstring = ast.get_docstring(node, clean=False)
        label = f"{relative}:{node.lineno} {node.name}"
        if not docstring:
            errors.append(f"{label}: missing docstring")
            continue
        if not CHINESE_PATTERN.search(docstring):
            errors.append(f"{label}: docstring must contain Chinese text")
        if not docstring.startswith("处理："):
            errors.append(f"{label}: docstring must start with 处理：")
        summary = docstring.splitlines()[0]
        if "``" in summary:
            errors.append(f"{label}: summary must explain behavior, not repeat its identifier")
        if len(CHINESE_PATTERN.findall(summary)) < 10:
            errors.append(f"{label}: summary is too short to explain the processing logic")
        for marker in ("输入：", "输出："):
            if marker not in docstring:
                errors.append(f"{label}: docstring is missing {marker}")
        if "输入：\n" not in docstring or not re.search(
            r"(?m)^\s*- (?:``[^`]+``|无显式业务参数)：.*[\u3400-\u9fff]",
            docstring,
        ):
            errors.append(f"{label}: inputs need named Chinese descriptions")
        for empty_pattern in EMPTY_DESCRIPTION_PATTERNS:
            if empty_pattern in docstring:
                errors.append(
                    f"{label}: docstring contains empty template wording {empty_pattern!r}"
                )
    return errors


def validate_code_comments(root: Path = ROOT) -> list[str]:
    """处理：汇总规范 Python 文件的函数、类注释错误。
    输入：
    - ``root``：安全边界或检查根目录；目标路径必须位于其中。
    输出：可操作的校验错误消息列表；空列表表示通过当前规则。
    """
    errors: list[str] = []
    for path in python_targets(root):
        if not path.is_file():
            errors.append(f"Missing Python target: {path.relative_to(root)}")
            continue
        errors.extend(definition_errors(path, root))
    return errors


def main() -> int:
    """处理：运行中文代码注释门禁并输出适合命令行阅读的结果。
    输入：
    - 无显式业务参数：不接收参数；扫描规范源码和维护脚本，检查每个定义的中文语义注释。
    输出：进程退出码；0 表示检查通过，非 0 表示存在已输出的错误。
    """
    errors = validate_code_comments()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "Chinese code-comment checks passed: "
        f"{len(python_targets())} maintained Python files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
