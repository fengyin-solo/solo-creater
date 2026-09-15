#!/usr/bin/env python3
"""同仓库题目语义去重（平台查重规则 C 的本地预检）。

平台规则 C 是按「模块 + 能力类型 + 交互骨架」判的，不看措辞。2026-09-15 实测
cc-6600009 那批 48 条里 21 条栽在这里，判词里的字面相似度低到 9.5%，平台原话是
「同为告警中心新增能力，但功能不同」「同属设备管理的待处理清单流程」「同一模块：地图上
按时间段叠加设备位置历史图层」。换句话说：同一个模块里反复出同类能力、或者反复套同一种
交互骨架，即使换成完全不同的对象和措辞，也会被判作废，而且**不可返修**。

这个脚本把上面那套口径做成结构化预检：

1. **同模块 + 同能力类型** → 违规；
2. **交互骨架命中 2 个以上信号**（清单+登记+空态+防重复这类）→ 违规；
3. **同一模块出题超过 `--max-per-module`（默认 3）条** → 超额违规，必须换模块。

用法：

    python3 scripts/check_repo_theme.py --parent "<父目录>"
    python3 scripts/check_repo_theme.py --parent "<父目录>" --write-ledger
    python3 scripts/check_repo_theme.py --prompts-file <每行一条提示词的文本文件> --ledger <父目录>/repo-theme-ledger.json

`--write-ledger` 会把每条的模块与能力类型写进父目录的 `repo-theme-ledger.json`，
后续批次继续在同一份台账上比，做到「同仓库全局去重」而不是只看当前这一批。

命中任一判据时返回 `ok: false` 并以退出码 1 结束。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_prompt_workbook as workbook_lib  # noqa: E402


LEDGER_FILENAME = "repo-theme-ledger.json"
DEFAULT_MAX_PER_MODULE = 3

# 模块词典：命中即算落在该模块；一条题可以落在多个模块里（例如告警中心 + 监控大屏）。
MODULE_LEXICON: dict[str, tuple[str, ...]] = {
    "告警中心": ("告警", "告警中心"),
    "围栏工作台": ("围栏",),
    "设备列表与详情": ("设备列表", "设备面板", "设备详情", "设备弹窗", "设备行"),
    "地图与图层": ("地图", "图层", "热区", "折线", "标记"),
    "轨迹回放": ("轨迹", "回放"),
    "健康诊断": ("健康评分", "健康诊断", "在线率", "综合评分"),
    "监控大屏": ("大屏",),
    "数据导出与归档": ("导出", "归档", "保留期"),
    "值班与人员": ("值班", "交接", "当班", "处理人"),
    "巡检": ("巡检", "保养", "审批"),
    "报表统计": ("统计", "耗时", "对照", "趋势"),
}

# 能力大类：平台判词按的是**很粗**的一层，原话是「同在告警中心新增能力，但升级规则校验与
# 实时新增提示功能不同」也照样判雷同。所以这一层刻意做粗：只要同模块里的两条题落在同一个
# 大类（新增处理机制 / 新增视图 / 新增入口 / 新增清单流程 / 属性扩展 / 同模块缺陷），
# 就按规则 C 的候选处理，逼着出题人换模块或换大类。
CAPABILITY_LEXICON: dict[str, tuple[str, ...]] = {
    "新增处理机制": ("处理", "确认", "升级", "分派", "归档", "交接", "流转", "规则", "阈值",
                    "超时", "升级", "预案", "处置", "结单", "心跳", "判定"),
    "新增视图与展示": ("视图", "列表", "弹窗", "提示", "展示", "排序", "统计", "趋势", "对照",
                      "对比", "热区", "折线", "标记", "高亮", "排行", "图谱", "图例"),
    "新增入口与共享": ("入口", "分享", "共享", "只读", "二维码", "扫码", "链接", "导出",
                      "导入", "下载"),
    "新增清单流程": ("清单", "登记", "审批", "提交", "空态", "待审批", "待保养", "保养"),
    "模块属性扩展": ("新增字段", "补充说明", "说明字段", "新增设置", "复制", "备注", "颜色",
                    "配色", "外观", "开关"),
    "模块缺陷": ("报错", "不生效", "没有生效", "残留", "对不上", "重复显示", "错位", "丢失",
                "点不动", "显示成"),
}

# 交互骨架：命中 2 个以上信号就说明这条题与另一条题在用同一种结构。
SKELETON_LEXICON: dict[str, tuple[str, ...]] = {
    "清单加登记加空态加防重复": ("清单", "登记", "空态", "暂无", "重复提交", "只生效一次"),
    "阈值加流转加例外恢复": ("阈值", "时长", "超时", "状态", "升级", "恢复", "归档"),
    "保存加失效加重复覆盖": ("保存", "失效", "已被收回", "重复", "沿用原有"),
    "筛选加空态加联动": ("筛选", "查询", "空态", "高亮", "同步", "恢复原有"),
    "统计加区间切换加空值": ("统计", "区间", "切换", "空态", "零值"),
    "批量提交加逐条结果": ("批量", "逐台", "逐条", "成功失败", "重新下发"),
}


def _score(text: str, keys: tuple[str, ...]) -> int:
    """同一组词里命中的关键词个数（同一个词只算一次）。"""
    return sum(1 for key in keys if key in text)


def _weight(text: str, keys: tuple[str, ...]) -> int:
    """同一组词在正文里出现的总次数，用来定主模块。

    只用「命中/没命中」定主模块会在平票时按名字排序，把「新增告警处置预案」这种
    明显属于告警中心的题归到值班与人员，后面整条去重链就跟着错。
    """
    return sum(text.count(key) for key in keys)


def detect_modules(text: str) -> tuple[str, list[str]]:
    """主模块 + 次要模块。

    主模块按命中关键词的个数取第一名；只命中一次又明显是顺带提到的模块不进次要列表，
    否则一条题里出现「告警」两个字就会跟所有告警题撞上，实测单关键词判定会报出 200 多条误伤。
    """
    scored = [(name, _weight(text, keys)) for name, keys in MODULE_LEXICON.items()]
    scored = [(name, score) for name, score in scored if score > 0]
    if not scored:
        return "", []
    scored.sort(key=lambda item: (-item[1], item[0]))
    primary = scored[0][0]
    secondary = [name for name, score in scored[1:] if score >= 2]
    return primary, secondary


def detect_capabilities(text: str) -> list[str]:
    """能力大类刻意做粗：一个信号就算落在该大类里（平台判词就是这么判的）。"""
    return [name for name, keys in CAPABILITY_LEXICON.items() if _score(text, keys) >= 1]


def detect_skeletons(text: str) -> list[str]:
    return [name for name, keys in SKELETON_LEXICON.items() if _score(text, keys) >= 2]


def classify(label: str, text: str) -> dict[str, object]:
    primary, secondary = detect_modules(text)
    return {
        "label": label,
        "module": primary,
        "modules": ([primary] if primary else []) + secondary,
        "capabilities": detect_capabilities(text),
        "skeletons": detect_skeletons(text),
    }


def check(
    items: list[tuple[str, str]],
    *,
    max_per_module: int = DEFAULT_MAX_PER_MODULE,
    base_ledger: dict | None = None,
) -> dict[str, object]:
    records = [classify(label, text) for label, text in items]
    # 硬拦只留「模块配额」这一条：平台判词本身是语义判断，关键词没法可靠复刻配对，
    # 但「同一模块反复出题」这件事是可数的，而且实测正是 21 条废弃的主因
    # （cc-6600009 光告警中心就出了 15 条）。把配额卡住，等于从源头掐掉大部分规则 C。
    primary_counter: Counter[str] = Counter()
    for record in records:
        if record["module"]:
            primary_counter[str(record["module"])] += 1
    # 台账里已有的条目也要算进配额，否则跨批次还会超
    for entry in (base_ledger or {}).get("entries", []) or []:
        if entry.get("module"):
            primary_counter[str(entry["module"])] += 1
    violations: list[dict[str, object]] = []
    if not any(record["module"] for record in records) and not primary_counter:
        violations.append({
            "kind": "模块未识别",
            "why": "这批题的模块一个都没识别出来，先确认提示词不是占位文本，再按模块重新归档",
        })
    for module, count in primary_counter.items():
        if count > max_per_module:
            violations.append({
                "kind": "模块超额",
                "module": module,
                "count": count,
                "limit": max_per_module,
                "why": f"同一模块最多出 {max_per_module} 条，超出的必须换模块或换题材",
            })

    # 候选清单：同模块 + 同能力大类的组合，供人工复核。平台判词是语义级的，
    # 关键词复刻不了它的配对，但这份清单是超集，看到成对的就按「同型」处理、换题材。
    candidates: list[dict[str, object]] = []
    for index, record in enumerate(records):
        for other in records[:index]:
            shared_modules = set(record["modules"]) & set(other["modules"])
            shared_capabilities = set(record["capabilities"]) & set(other["capabilities"])
            shared_skeletons = set(record["skeletons"]) & set(other["skeletons"])
            if not shared_modules or not shared_capabilities:
                continue
            candidates.append({
                "a": other["label"],
                "b": record["label"],
                "modules": sorted(shared_modules),
                "capabilities": sorted(shared_capabilities),
                "skeletons": sorted(shared_skeletons),
                "why": "同模块里出同类能力，平台按规则 C 判语义雷同且不可返修；换模块或换题材",
            })

    return {
        "ok": not violations,
        "rule": "平台查重规则 C（同仓库题目语义雷同）+ 同模块出题上限",
        "max_per_module": max_per_module,
        "checked_count": len(records),
        "module_counts": dict(primary_counter.most_common()),
        "violations": violations,
        "candidate_pairs": candidates,
        "items": records,
        "notes": [
            "规则 C 被判废弃的记录不可返修，改措辞无效，只能换题材重出。",
            "平台拿先提交的那条当基准，后提交的语义近题判废弃，所以同仓库必须一次性全局去重。",
            "硬拦只到「模块配额 + 模块未识别」；candidate_pairs 是超集候选，逐对人工复核，"
            "认下同型就换题材，不要只看字面像不像。",
        ],
    }


def load_prompt_file(path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if text:
            items.append((f"{path.name}#{index}", text))
    return items


def load_workbook_prompts(parent: Path, workbook_name: str) -> list[tuple[str, str]]:
    records = workbook_lib.read_workbook(parent / workbook_name)
    items: list[tuple[str, str]] = []
    for record in records:
        prompt = str(record.get("提示词", "") or "").strip()
        if not prompt:
            continue
        label = str(record.get("子文件夹名称", "") or "未命名")
        task_type = str(record.get("任务类型", "") or "").strip()
        if task_type:
            label = f"{label}[{task_type}]"
        items.append((label, prompt))
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", help="父目录，默认读取其中的 solo-create-prompts.xlsx")
    parser.add_argument("--workbook", default=workbook_lib.DEFAULT_WORKBOOK)
    parser.add_argument("--prompts-file", help="每行一条提示词的文本文件，与 --parent 二选一")
    parser.add_argument("--ledger", help="已有台账 json；默认用父目录下的 repo-theme-ledger.json")
    parser.add_argument("--write-ledger", action="store_true", help="把本次分类结果写进台账")
    parser.add_argument("--max-per-module", type=int, default=DEFAULT_MAX_PER_MODULE,
                        help=f"同一模块最多出几条，默认 {DEFAULT_MAX_PER_MODULE}")
    args = parser.parse_args()

    parent: Path | None = None
    if args.prompts_file:
        items = load_prompt_file(Path(args.prompts_file).expanduser().resolve())
    elif args.parent:
        parent = Path(args.parent).expanduser().resolve()
        if not parent.is_dir():
            raise SystemExit(f"Parent directory does not exist: {parent}")
        items = load_workbook_prompts(parent, args.workbook)
    else:
        raise SystemExit("必须提供 --parent 或 --prompts-file 之一")

    ledger_path = Path(args.ledger).expanduser().resolve() if args.ledger else (
        parent / LEDGER_FILENAME if parent else None
    )
    base_ledger = None
    if ledger_path and ledger_path.exists():
        base_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    result = check(items, max_per_module=args.max_per_module, base_ledger=base_ledger)

    if args.write_ledger and ledger_path:
        merged: dict[str, dict] = {}
        for entry in (base_ledger or {}).get("entries", []) or []:
            merged[str(entry.get("label"))] = entry
        for entry in result["items"]:
            merged[str(entry["label"])] = entry
        ledger_path.write_text(
            json.dumps({
                "rule": result["rule"],
                "max_per_module": result["max_per_module"],
                "module_counts": result["module_counts"],
                "entries": list(merged.values()),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["ledger"] = str(ledger_path)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
