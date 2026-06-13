#!/usr/bin/env python3
"""Maintain the batch prompt workbook for solo-create."""

from __future__ import annotations

import argparse
import json
import re
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
) -> dict[str, str]:
    parsed = parse_folder_name(folder)
    if not parsed:
        raise SystemExit(f"Cannot parse folder name: {folder}")
    path = workbook_path(parent, workbook)
    records = read_workbook(path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = None
    for index, record in enumerate(records):
        if record.get("子文件夹名称") == folder and record.get("提示词类型", "主提示词") != "修复提示词":
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
        row["提示词类型"] = prompt_type or ("主提示词" if prompt else "")
        row["状态"] = status or ("已生成" if prompt else "待生成")
        row["备注"] = note
        row["更新时间"] = now
        records.append(row)
    write_workbook(path, records)
    return {"workbook": str(path), "folder": folder, "status": row["状态"]}


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
        result = update(parent, args.workbook, args.folder, args.prompt, args.note, args.status, args.prompt_type)
    else:
        result = pick(parent, args.workbook, args.range, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
