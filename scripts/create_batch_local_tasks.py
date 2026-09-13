#!/usr/bin/env python3
"""批量建仓路的本地脚手架脚本。

只做三件事：

1. 把父目录下唯一的源代码目录识别成原始代码，并确认它绑定了 Git。
2. 按固定命名和数量在父目录下批量创建任务目录，每个任务目录固定包含
   `origin`（原始代码副本，不含 `.git`）和 `workspace`（空目录）。
3. 在父目录维护 `solo-create-prompts.xlsx`，格式与既有生成提示词工作簿一致。

本脚本不创建 GitHub 仓库，不执行 `git push`，不改动也不提交源仓库。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_prompt_workbook as workbook_lib  # noqa: E402


COPY_IGNORE = {".git", ".DS_Store"}
DEFAULT_WORKBOOK = "solo-create-prompts.xlsx"


@dataclass(frozen=True)
class TaskSpec:
    slug: str
    task_type: str
    count: int


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def is_git_repository(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / ".git").exists():
        return True
    result = run_command(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def candidate_source_dirs(parent: Path) -> list[Path]:
    """父目录下所有非隐藏、且不是已命名任务目录的一级子目录。"""
    candidates: list[Path] = []
    for child in sorted(parent.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or is_hidden(child.name):
            continue
        if workbook_lib.parse_folder_name(child.name):
            continue
        candidates.append(child)
    return candidates


def resolve_source(parent: Path, source_arg: str | None) -> Path:
    if source_arg:
        source = Path(source_arg).expanduser().resolve()
        if not source.is_dir():
            raise SystemExit(f"Source directory does not exist: {source}")
        if source.parent != parent:
            raise SystemExit("Source directory must be an immediate child of parent")
        return source
    candidates = candidate_source_dirs(parent)
    if not candidates:
        raise SystemExit(
            f"Parent has no candidate source directory: {parent}. "
            "父目录下必须只有一个子目录，并且它就是原始代码。"
        )
    if len(candidates) > 1:
        names = ", ".join(item.name for item in candidates)
        raise SystemExit(
            f"Parent has more than one candidate source directory: {names}. "
            "请只保留一个原始代码子目录，或用 --source 明确指定。"
        )
    return candidates[0]


def extract_number(text: str) -> str | None:
    match = re.search(r"\d{3,}", text)
    return match.group(0) if match else None


def resolve_source_number(source: Path, parent: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in (source.name, parent.name):
        number = extract_number(candidate)
        if number:
            return number
    raise SystemExit(
        "Cannot infer source project number from directory names. Rerun with --source-number."
    )


def build_specs(args: argparse.Namespace) -> list[TaskSpec]:
    return [
        TaskSpec("codegen", "代码生成", args.codegen_count),
        TaskSpec("feature", "功能迭代", args.feature_count),
        TaskSpec("understand", "代码理解", args.understand_count),
        TaskSpec("refactor", "代码重构", args.refactor_count),
        TaskSpec("engineering", "工程化", args.engineering_count),
    ]


def task_name(source_number: str, sequence: int, slug: str, name_style: str) -> str:
    if name_style == "dash":
        return f"{source_number}-{slug}-{sequence}"
    return f"{source_number}{sequence}-{slug}-{sequence}"


def difficulty_of(sequence: int) -> str:
    # 编号奇数取 地狱、偶数取 困难，整体各占一半。
    return "地狱" if sequence % 2 else "困难"


def planned_tasks(
    source_number: str, specs: list[TaskSpec], name_style: str
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    sequence = 1
    remaining = {spec.slug: spec.count for spec in specs}
    primary_slugs = ["codegen", "feature"]
    task_types = {spec.slug: spec.task_type for spec in specs}

    while any(remaining.get(slug, 0) > 0 for slug in primary_slugs):
        for slug in primary_slugs:
            for _ in range(min(5, remaining.get(slug, 0))):
                items.append(
                    {
                        "name": task_name(source_number, sequence, slug, name_style),
                        "task_slug": slug,
                        "task_type": task_types[slug],
                        "index": sequence,
                        "difficulty": difficulty_of(sequence),
                    }
                )
                sequence += 1
                remaining[slug] -= 1

    for spec in specs:
        if spec.slug in primary_slugs:
            continue
        for _ in range(remaining.get(spec.slug, 0)):
            items.append(
                {
                    "name": task_name(source_number, sequence, spec.slug, name_style),
                    "task_slug": spec.slug,
                    "task_type": spec.task_type,
                    "index": sequence,
                    "difficulty": difficulty_of(sequence),
                }
            )
            sequence += 1
    return items


def copy_code(source: Path, origin: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in COPY_IGNORE}

    shutil.copytree(source, origin, ignore=ignore, symlinks=True)


def sync_workbook(
    parent: Path, workbook_name: str, tasks: list[dict[str, object]]
) -> tuple[Path, list[str]]:
    path = parent / workbook_name
    records = workbook_lib.read_workbook(path)
    known = {record["子文件夹名称"] for record in records if record.get("子文件夹名称")}
    planned = {str(task["name"]) for task in tasks}
    unmatched = sorted(folder for folder in known if folder not in planned)
    for task in tasks:
        folder = str(task["name"])
        if folder in known:
            continue
        records.append(
            workbook_lib.blank_record(
                {"folder": folder, "task_type": str(task["task_type"]), "number": str(task["index"])}
            )
        )
        known.add(folder)
    workbook_lib.write_workbook(path, records)
    return path, unmatched


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", default=".", help="Parent directory that holds the source and task folders")
    parser.add_argument("--source", help="Source directory; defaults to the only child directory of parent")
    parser.add_argument("--source-number", help="Project number used in task folder names")
    parser.add_argument("--name-style", choices=["concat", "dash"], default="concat",
                        help="concat: <编号><序号>-<标识>-<序号>（默认，沿用现有规则）；dash: <编号>-<标识>-<序号>")
    parser.add_argument("--codegen-count", type=int, default=30)
    parser.add_argument("--feature-count", type=int, default=30)
    parser.add_argument("--understand-count", type=int, default=1)
    parser.add_argument("--refactor-count", type=int, default=1)
    parser.add_argument("--engineering-count", type=int, default=1)
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    parent = Path(args.parent).expanduser().resolve()
    if not parent.is_dir():
        raise SystemExit(f"Parent directory does not exist: {parent}")

    source = resolve_source(parent, args.source)

    # 源目录必须绑定 Git；不满足时在建任何目录、写任何工作簿之前停下。
    if not is_git_repository(source):
        raise SystemExit(
            "SOURCE_NOT_GIT: 原始代码目录没有绑定 Git，已停止所有操作。"
            f" source={source}。请先在该目录初始化 Git 仓库，再重新运行批量建仓。"
        )

    source_number = resolve_source_number(source, parent, args.source_number)
    specs = build_specs(args)
    tasks = planned_tasks(source_number, specs, args.name_style)

    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for task in tasks:
        folder = str(task["name"])
        target = parent / folder
        if target.exists():
            skipped.append({"folder": folder, "reason": "local target exists"})
            continue
        if args.dry_run:
            created.append({"folder": folder, "status": "planned"})
            continue
        try:
            target.mkdir(parents=True)
            origin = target / "origin"
            copy_code(source, origin)
            (target / "workspace").mkdir()
            if (origin / ".git").exists():
                raise RuntimeError("origin still contains git metadata")
            if any((target / "workspace").iterdir()):
                raise RuntimeError("workspace is not empty")
            created.append({"folder": folder, "status": "created"})
        except Exception as exc:  # noqa: BLE001
            failed.append({"folder": folder, "reason": str(exc)})

    difficulty_split: dict[str, int] = {}
    for task in tasks:
        key = str(task["difficulty"])
        difficulty_split[key] = difficulty_split.get(key, 0) + 1

    workbook_path = str(parent / args.workbook)
    workbook_row_count = 0
    workbook_unmatched: list[str] = []
    if not args.dry_run:
        synced, workbook_unmatched = sync_workbook(parent, args.workbook, tasks)
        workbook_path = str(synced)
        workbook_row_count = len(workbook_lib.read_workbook(Path(workbook_path)))
    else:
        existing = workbook_lib.read_workbook(parent / args.workbook)
        planned = {str(task["name"]) for task in tasks}
        workbook_unmatched = sorted(
            record["子文件夹名称"]
            for record in existing
            if record.get("子文件夹名称") and record["子文件夹名称"] not in planned
        )

    print(
        json.dumps(
            {
                "parent": str(parent),
                "source": str(source),
                "source_number": source_number,
                "name_style": args.name_style,
                "difficulty_rule": "编号奇数=地狱，偶数=困难",
                "difficulty_split": difficulty_split,
                "dry_run": args.dry_run,
                "planned_count": len(tasks),
                "created_count": len(created) if not args.dry_run else 0,
                "dry_run_count": len(created) if args.dry_run else 0,
                "skipped_count": len(skipped),
                "failed_count": len(failed),
                "workbook": workbook_path,
                "workbook_row_count": workbook_row_count,
                "workbook_unmatched_count": len(workbook_unmatched),
                "workbook_unmatched": workbook_unmatched[:10],
                "created": created,
                "skipped": skipped,
                "failed": failed,
                "next_step": "generate_prompts",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
