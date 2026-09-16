#!/usr/bin/env python3
"""Maintain the batch prompt workbook for solo-create."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


HEADERS = [
    "子文件夹名称",
    "任务类型",
    "编号",
    "提示词",
    "提示词类型",
    "状态",
    "备注",
    "更新时间",
]
DEFAULT_WORKBOOK = "solo-create-prompts.xlsx"
# 写入前的闸门：这三道不通过就不许把提示词写进工作簿（除非显式 --allow-gate-failure）
GATE_TAG = "生成规则"
MANIFEST_FILENAME = "prompt-generation-manifest.json"
EXEMPT_TASK_TYPES = {"工程化", "代码理解", "代码重构"}
NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def canonical_task_type(raw: str) -> str | None:
    text = raw.strip()
    lower = text.lower()
    if "缺陷" in text or "bug" in lower:
        return "缺陷修复"
    if "功能" in text or "feature" in lower:
        return "功能迭代"
    if "生成" in text or "codegen" in lower or lower == "code":
        return "代码生成"
    if "理解" in text or "understand" in lower:
        return "代码理解"
    if "重构" in text or "refactor" in lower:
        return "代码重构"
    if "工程" in text or "engineering" in lower:
        return "工程化"
    return None


def parse_folder_name(name: str) -> dict[str, str] | None:
    parts = name.split("-")
    if len(parts) < 3:
        return None
    number = parts[-1].strip()
    if not re.fullmatch(r"\d+", number):
        return None
    for part in reversed(parts[:-1]):
        task_type = canonical_task_type(part)
        if task_type:
            return {"folder": name, "task_type": task_type, "number": number}
    return None


def col_name(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//a:t", NS))
    value = cell.find("a:v", NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return ""
    return value.text


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("a:si", NS):
        strings.append("".join(node.text or "" for node in item.findall(".//a:t", NS)))
    return strings


def read_workbook(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in zf.namelist():
            return []
        root = ET.fromstring(zf.read(sheet_name))
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", NS):
        values = [""] * len(HEADERS)
        for cell in row.findall("a:c", NS):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            index = 0
            for char in match.group(1):
                index = index * 26 + ord(char) - 64
            if 1 <= index <= len(HEADERS):
                values[index - 1] = cell_text(cell, shared_strings)
        rows.append(values)
    if not rows:
        return []
    headers = rows[0]
    records: list[dict[str, str]] = []
    for values in rows[1:]:
        record = {header: values[i] if i < len(values) else "" for i, header in enumerate(headers)}
        if any(record.values()):
            records.append(normalize_record(record))
    return records


def write_workbook(path: Path, records: list[dict[str, str]]) -> None:
    rows = [HEADERS] + [[normalize_record(record).get(header, "") for header in HEADERS] for record in records]
    sheet_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            ref = f"{col_name(col_index)}{row_index}"
            text = escape(str(value), {'"': "&quot;"})
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<cols><col min="1" max="1" width="34" customWidth="1"/><col min="2" max="3" width="14" customWidth="1"/><col min="4" max="4" width="90" customWidth="1"/><col min="5" max="7" width="18" customWidth="1"/><col min="8" max="8" width="20" customWidth="1"/></cols>
<sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>'''
    files = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="prompts" sheetId="1" r:id="rId1"/></sheets>
</workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''',
        "xl/worksheets/sheet1.xml": sheet_xml,
        "docProps/app.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>solo-create</Application></Properties>''',
        "docProps/core.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator>solo-create</dc:creator><cp:lastModifiedBy>solo-create</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{datetime.now(timezone.utc).isoformat()}</dcterms:created>
</cp:coreProperties>''',
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def workbook_path(parent: Path, workbook: str | None) -> Path:
    return parent / (workbook or DEFAULT_WORKBOOK)


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    prompt = record.get("提示词", "").strip()
    prompt_type = record.get("提示词类型", "").strip()
    if not prompt_type and prompt:
        prompt_type = "主提示词"
    normalized = {header: "" for header in HEADERS}
    for header in HEADERS:
        if header == "提示词类型":
            normalized[header] = prompt_type
            continue
        normalized[header] = record.get(header, "")
    return normalized


def blank_record(parsed: dict[str, str]) -> dict[str, str]:
    return {
        "子文件夹名称": parsed["folder"],
        "任务类型": parsed["task_type"],
        "编号": parsed["number"],
        "提示词": "",
        "提示词类型": "",
        "状态": "待生成",
        "备注": "",
        "更新时间": "",
    }


def record_number(record: dict[str, str]) -> int:
    number = record.get("number") or record.get("编号") or ""
    return int(number) if str(number).isdigit() else 0


def sort_pending_for_batch_generation(pending: list[dict[str, str]]) -> list[dict[str, str]]:
    by_type: dict[str, list[dict[str, str]]] = {}
    for item in pending:
        by_type.setdefault(item.get("task_type", ""), []).append(item)
    for items in by_type.values():
        items.sort(key=record_number)

    ordered: list[dict[str, str]] = []
    primary_types = ["代码生成", "功能迭代"]
    while any(by_type.get(task_type) for task_type in primary_types):
        for task_type in primary_types:
            items = by_type.get(task_type, [])
            if not items:
                continue
            ordered.extend(items[:5])
            del items[:5]

    for task_type in sorted(task_type for task_type in by_type if task_type not in primary_types):
        ordered.extend(by_type[task_type])
    return ordered


def detect_repo(parent: Path) -> Path | None:
    """父目录里任意任务目录下的 origin 就是这道题的仓库，用来派生模块词典。"""
    try:
        children = sorted(path for path in parent.iterdir() if path.is_dir())
    except OSError:
        return None
    for child in children:
        origin = child / "origin"
        if origin.is_dir():
            return origin
    return None


def skill_freshness(skill_root: Path | None = None) -> dict:
    """skill 是否落后于它的 origin：落后就拒跑，避免拿旧规则出题。

    2026-09-16：cc-6600025 那批是在新规则推上去 40 分钟后生成的，然而闸门只写在
    SKILL.md 里、写入脚本不校验，脏数据照样落表。这里把「先更新 skill」变成机械前置。
    """
    root = Path(skill_root or Path(__file__).resolve().parents[1])
    info: dict[str, object] = {"skill_root": str(root)}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15)
        branch = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=15)
        info["head"] = (head.stdout or "").strip()
        info["branch"] = (branch.stdout or "").strip() or "HEAD"
        if head.returncode != 0:
            info["checked"] = False
            return info
        behind = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", f"HEAD..origin/{info['branch']}"],
            capture_output=True, text=True, timeout=15,
        )
        if behind.returncode != 0:
            info["checked"] = False
            info["note"] = "没有远端跟踪分支，跳过版本比较"
            return info
        info["checked"] = True
        info["behind"] = int((behind.stdout or "0").strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:  # noqa: BLE001
        info["checked"] = False
        info["note"] = f"版本检查跳过：{exc}"
    return info


def run_write_gates(
    parent: Path,
    records: list[dict[str, str]],
    *,
    repo: Path | None,
    max_per_module: int = 3,
    judgement_corpus: dict | None = None,
    min_candidate_recall: float | None = None,
) -> dict:
    """写表前的三道闸：同仓库主题去重、难度下限、提示词查重。

    三道都跑在「这份工作簿加上本次要写的那条提示词」之后的整批内容上，
    任何一道不通过就返回 ok=False，由调用方决定拒写还是显式放行。
    """
    from check_prompt_dedup import check as dedup_check
    from check_prompt_difficulty import JUSTIFICATION_PREFIX
    from check_prompt_difficulty import check as difficulty_check
    from check_repo_theme import (
        GATE_VERSION,
        LEDGER_FILENAME,
        check as theme_check,
        derive_repo_modules,
        load_ledger,
        merge_ledgers,
        repo_ledger_path,
    )
    from check_repo_theme import DEFAULT_CORPUS_PATH

    if judgement_corpus is None and DEFAULT_CORPUS_PATH.exists():
        try:
            judgement_corpus = json.loads(DEFAULT_CORPUS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            judgement_corpus = None

    items: list[tuple[str, str]] = []
    repair_labels: set[str] = set()
    exempt_labels: set[str] = set()
    justifications: dict[str, str] = {}
    for record in records:
        prompt = str(record.get("提示词") or "").strip()
        if not prompt:
            continue
        task_type = str(record.get("任务类型") or "").strip()
        label = f"{record.get('子文件夹名称') or '未命名'}[{task_type}]"
        items.append((label, prompt))
        if task_type in {"Bug 修复", "Bug修复"} or str(record.get("提示词类型") or "").startswith("修复"):
            repair_labels.add(label)
        if task_type in EXEMPT_TASK_TYPES:
            exempt_labels.add(label)
        note = str(record.get("备注") or "").strip()
        if note.startswith(JUSTIFICATION_PREFIX):
            justifications[label] = note

    derived = derive_repo_modules(repo) if repo else {}
    base_ledger = merge_ledgers(
        load_ledger(parent / LEDGER_FILENAME),
        load_ledger(repo_ledger_path(repo)) if repo else {},
    )
    theme = theme_check(
        items,
        max_per_module=max_per_module,
        judgement_corpus=judgement_corpus,
        min_candidate_recall=min_candidate_recall,
        base_ledger=base_ledger,
        derived=derived,
        repair_labels=repair_labels,
        exempt_labels=exempt_labels,
        require_ledger=True,
        ledger_present=(parent / LEDGER_FILENAME).exists(),
        max_unknown=0,
    )
    defect_labels = {label for label, _ in items if "缺陷修复" in label or "Bug 修复" in label}
    difficulty = difficulty_check(
        items, 2,
        defect_structure=True,
        defect_labels=defect_labels,
        justifications=justifications,
    )
    dedup = dedup_check(items, 0.2)

    problems: list[str] = []
    for violation in theme.get("violations", []) or []:
        kind = violation.get("kind")
        detail = (
            violation.get("subject_mode") or violation.get("subject") or violation.get("mode")
            or violation.get("labels") or ""
        )
        problems.append(f"主题去重未通过：{kind} {detail}".strip())
    for violation in difficulty.get("violations", []) or []:
        problems.append(f"难度下限未通过：{violation.get('label')} {'；'.join(violation.get('reasons') or [])}")
    for violation in dedup.get("violations", []) or []:
        problems.append(
            f"提示词查重未通过：{violation.get('left')} 与 {violation.get('right')} "
            f"{violation.get('kind')} {violation.get('score')}"
        )
    return {
        "ok": not problems,
        "gate_version": GATE_VERSION,
        "problems": problems,
        "theme": {
            "ok": theme.get("ok"),
            "module_counts": theme.get("module_counts"),
            "mode_counts": theme.get("mode_counts"),
            "subject_mode_counts": theme.get("subject_mode_counts"),
            "task_type_counts": theme.get("task_type_counts"),
            "new_capability_ratio": theme.get("new_capability_ratio"),
            "calibration": theme.get("calibration"),
            "limits": theme.get("limits"),
            "violations": theme.get("violations"),
            "candidate_pairs": (theme.get("candidate_pairs") or [])[:20],
            "candidate_pair_count": theme.get("candidate_pair_count"),
            "review_required_labels": theme.get("review_required_labels"),
            "review_list": (theme.get("review_list") or [])[:60],
            "unknown_labels": theme.get("unknown_labels"),
        },
        "difficulty": {"ok": difficulty.get("ok"), "violations": difficulty.get("violations"),
                       "needs_review": difficulty.get("needs_review")},
        "dedup": {"ok": dedup.get("ok"), "violations": dedup.get("violations")},
    }


def write_generation_manifest(
    parent: Path,
    *,
    workbook: Path,
    repo: Path | None,
    gate: dict,
    skill: dict,
) -> Path:
    """把这一批的生成规则版本与闸门结论落一份清单，跨机器可追溯。"""
    path = parent / MANIFEST_FILENAME
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "workbook": str(workbook),
        "repo": str(repo) if repo else "",
        "gate_version": gate.get("gate_version"),
        "gate_ok": gate.get("ok"),
        "module_counts": (gate.get("theme") or {}).get("module_counts"),
        "subject_mode_counts": (gate.get("theme") or {}).get("subject_mode_counts"),
        "mode_counts": (gate.get("theme") or {}).get("mode_counts"),
        "task_type_counts": (gate.get("theme") or {}).get("task_type_counts"),
        "new_capability_ratio": (gate.get("theme") or {}).get("new_capability_ratio"),
        "limits": (gate.get("theme") or {}).get("limits"),
        "calibration": (gate.get("theme") or {}).get("calibration"),
        "review_required_labels": (gate.get("theme") or {}).get("review_required_labels"),
        "violations": gate.get("problems") or [],
        "skill": skill,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def scan(parent: Path, workbook: str | None) -> dict[str, object]:
    path = workbook_path(parent, workbook)
    records = read_workbook(path)
    by_folder = {record["子文件夹名称"]: record for record in records if record.get("子文件夹名称")}
    discovered: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for child in sorted(parent.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        parsed = parse_folder_name(child.name)
        if not parsed:
            continue
        discovered.append(parsed)
        existing = by_folder.get(child.name)
        if existing and existing.get("提示词", "").strip():
            skipped.append(parsed)
        else:
            pending.append(parsed)
            if not existing:
                records.append(blank_record(parsed))
    pending = sort_pending_for_batch_generation(pending)
    write_workbook(path, records)
    return {
        "workbook": str(path),
        "discovered_count": len(discovered),
        "pending_count": len(pending),
        "skipped_count": len(skipped),
        "pending": pending,
        "skipped": skipped,
    }


def update(
    parent: Path,
    workbook: str | None,
    folder: str,
    prompt: str | None,
    note: str,
    status: str | None,
    prompt_type: str | None,
    *,
    repo: Path | None = None,
    allow_gate_failure: bool = False,
    skip_version_check: bool = False,
) -> dict:
    parsed = parse_folder_name(folder)
    if not parsed:
        raise SystemExit(f"Cannot parse folder name: {folder}")
    path = workbook_path(parent, workbook)
    records = read_workbook(path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    desired_prompt_type = (prompt_type or "").strip()
    row = None
    if desired_prompt_type == "修复提示词":
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            if record.get("子文件夹名称") != folder:
                continue
            if (record.get("提示词类型", "主提示词").strip() or "主提示词") != "修复提示词":
                continue
            row = record
            if prompt is not None:
                row["提示词"] = prompt
                row["提示词类型"] = "修复提示词"
            if status is not None:
                row["状态"] = status
            elif prompt is not None:
                row["状态"] = "已生成"
            if note:
                row["备注"] = note
            row["更新时间"] = now
            records[index] = row
            break
    else:
        for index, record in enumerate(records):
            if record.get("子文件夹名称") != folder:
                continue
            if (record.get("提示词类型", "主提示词").strip() or "主提示词") == "修复提示词":
                continue
            row = record
            if prompt is not None:
                row["提示词"] = prompt
                row["提示词类型"] = prompt_type or row.get("提示词类型") or "主提示词"
            if status is not None:
                row["状态"] = status
            elif prompt is not None:
                row["状态"] = "已生成"
            if note:
                row["备注"] = note
            row["更新时间"] = now
            records[index] = row
            break
    if row is None:
        row = blank_record(parsed)
        row["提示词"] = prompt or ""
        row["提示词类型"] = desired_prompt_type or ("主提示词" if prompt else "")
        row["状态"] = status or ("已生成" if prompt else "待生成")
        row["备注"] = note
        row["更新时间"] = now
        records.append(row)

    gate: dict = {}
    manifest = None
    if prompt is not None:
        repo_path = repo or detect_repo(parent)
        skill = {} if skip_version_check else skill_freshness()
        if skill.get("checked") and int(skill.get("behind") or 0) > 0:
            raise SystemExit(
                "solo-create 这个 skill 落后于远端 "
                f"{skill.get('behind')} 个提交（{skill.get('skill_root')}），先 git pull --ff-only 再出题；"
                "确实要用当前版本就加 --skip-version-check"
            )
        gate = run_write_gates(parent, records, repo=repo_path)
        gate["skill"] = skill
        if not gate["ok"] and not allow_gate_failure:
            print(json.dumps({
                "ok": False,
                "error": "写入前的闸门没有通过，这条提示词没有写进工作簿",
                "gate_version": gate.get("gate_version"),
                "problems": gate.get("problems"),
                "theme": gate.get("theme"),
                "difficulty": gate.get("difficulty"),
                "dedup": gate.get("dedup"),
                "hint": "先按上面的违规项换主体 / 换需求模式 / 调类型配比重写；"
                        "review_required_labels 里的每条题都要写出与最近邻的差异，写不出就换题。"
                        "确实要先落表再加 --allow-gate-failure，但被平台判废弃的记录不可返修",
            }, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        tag = f"{GATE_TAG}: {gate.get('gate_version')}；闸门: {'ok' if gate['ok'] else '强制放行'}"
        row["备注"] = f"{row.get('备注') or ''}；{tag}".strip("；") if row.get("备注") else tag
        manifest = write_generation_manifest(
            parent, workbook=path, repo=repo_path, gate=gate, skill=skill,
        )

    write_workbook(path, records)
    result: dict = {"workbook": str(path), "folder": folder, "status": row["状态"]}
    if gate:
        result["gate"] = {
            "ok": gate["ok"],
            "gate_version": gate.get("gate_version"),
            "problems": gate.get("problems"),
            "module_counts": (gate.get("theme") or {}).get("module_counts"),
            "mode_counts": (gate.get("theme") or {}).get("mode_counts"),
            "subject_mode_counts": (gate.get("theme") or {}).get("subject_mode_counts"),
            "new_capability_ratio": (gate.get("theme") or {}).get("new_capability_ratio"),
            "candidate_pair_count": (gate.get("theme") or {}).get("candidate_pair_count"),
            "review_required_labels": (gate.get("theme") or {}).get("review_required_labels"),
            "candidate_pairs": (gate.get("theme") or {}).get("candidate_pairs"),
            "needs_review": (gate.get("difficulty") or {}).get("needs_review"),
        }
        result["manifest"] = str(manifest) if manifest else ""
    return result


def parse_number_range(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*(?:-|~|至|到)\s*(\d+)\s*", value)
    if match:
        start = int(match.group(1))
        end = int(match.group(2))
        if start > end:
            start, end = end, start
        return start, end
    if re.fullmatch(r"\s*\d+\s*", value):
        number = int(value)
        return number, number
    raise SystemExit(f"Invalid range: {value}")


def pick(parent: Path, workbook: str | None, number_range: str | None, limit: int | None) -> dict[str, object]:
    path = workbook_path(parent, workbook)
    records = read_workbook(path)
    parsed_range = parse_number_range(number_range)
    picked: list[dict[str, str]] = []
    for record in records:
        prompt = record.get("提示词", "").strip()
        number_text = record.get("编号", "").strip()
        prompt_type = record.get("提示词类型", "主提示词").strip() or "主提示词"
        if not prompt or not number_text.isdigit():
            continue
        if prompt_type != "主提示词":
            continue
        number = int(number_text)
        if parsed_range and not (parsed_range[0] <= number <= parsed_range[1]):
            continue
        picked.append(record)
    picked.sort(key=lambda item: int(item.get("编号") or 0))
    if limit is not None:
        picked = picked[:limit]
    return {"workbook": str(path), "count": len(picked), "items": picked}


def locate_project(project: Path, workbook: str | None) -> dict[str, object]:
    project = project.expanduser().resolve()
    project_candidates = [project, *project.parents]
    workbook_candidates = [project.parent, *project.parents]
    seen: set[Path] = set()
    for parent in workbook_candidates:
        if parent in seen:
            continue
        seen.add(parent)
        path = workbook_path(parent, workbook)
        if not path.exists():
            continue
        records = read_workbook(path)
        for record in reversed(records):
            folder = record.get("子文件夹名称")
            for candidate in project_candidates:
                if candidate.name == folder:
                    return {
                        "found": True,
                        "project": str(project),
                        "matched_project": str(candidate),
                        "parent": str(parent),
                        "workbook": str(path),
                        "record": record,
                    }
    return {"found": False, "project": str(project), "folder": project.name}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "update", "pick", "locate-project"])
    parser.add_argument("--parent")
    parser.add_argument("--project")
    parser.add_argument("--workbook")
    parser.add_argument("--folder")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-type")
    parser.add_argument("--note", default="")
    parser.add_argument("--status")
    parser.add_argument("--range")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repo", help="仓库路径；默认自动找父目录下任意任务目录的 origin")
    parser.add_argument("--allow-gate-failure", action="store_true",
                        help="闸门不通过也强行写表（默认拒写；被平台判废弃的记录不可返修，慎用）")
    parser.add_argument("--skip-version-check", action="store_true",
                        help="跳过「skill 是否落后于远端」的检查")
    args = parser.parse_args()

    if args.command == "locate-project":
        result = locate_project(Path(args.project or "."), args.workbook)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not args.parent:
        raise SystemExit(f"{args.command} requires --parent")
    parent = Path(args.parent).expanduser().resolve()
    if args.command == "scan":
        result = scan(parent, args.workbook)
    elif args.command == "update":
        if not args.folder:
            raise SystemExit("update requires --folder")
        result = update(
            parent, args.workbook, args.folder, args.prompt, args.note, args.status, args.prompt_type,
            repo=Path(args.repo).expanduser().resolve() if args.repo else None,
            allow_gate_failure=args.allow_gate_failure,
            skip_version_check=args.skip_version_check,
        )
    else:
        result = pick(parent, args.workbook, args.range, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
