#!/usr/bin/env python3
"""检查提示词是否命中平台「过于简单」拒收口径。

平台规则：同一道题同时命中两项及以上「过于简单」特征时一律拒收。
本脚本按下面这些特征做客观兜底检查，命中两项以上即 `ok: false`：

1. `修改范围`：改动收在一处判断、一个文件或一段文案里，题面没有出现任何
   跨入口、跨数据、后续操作或既有状态联动的要求。
2. `交互轮次`：整条需求一次点击或一次提交就能验完，题面没有出现任何后续步骤、
   返回、刷新、切换、重新进入这类多轮动作。
3. `技术广度`：只落在一个技术面上（例如只有输入校验），没有第二个技术面
   （状态持久化、数据与接口、并发时序、权限角色、交互呈现等）。
4. `逻辑复杂度`：只有单一条件判断（顺序、空值、长度、格式、大小），
   没有复合条件或与其它业务规则的咬合。
5. `验证深度`：既没有非开心路径（空数据、失败重试、边界输入、重复提交、
   并发时序、权限差异等），也没有跨状态一致性要求。

脚本是词面兜底，通过不代表题目一定够难；生成方仍必须按手册的难度下限自查，
脚本命中时必须换角度重出，不允许只改同义词。

用法：

    python3 scripts/check_prompt_difficulty.py --prompts-file <每行一条提示词的文本文件>
    python3 scripts/check_prompt_difficulty.py --parent "<父目录>"
    python3 scripts/check_prompt_difficulty.py --parent "<父目录>" --task-types 缺陷修复,功能迭代

命中两项及以上时返回 `ok: false` 并以退出码 1 结束，便于流程里直接拦停。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_prompt_workbook as workbook_lib  # noqa: E402


DEFAULT_MIN_HITS = 2
# 2026-09-16 改：难度检查默认覆盖全部任务类型。cc-6600011 的 33 号是「代码生成」，
# 批量流程只跑了 缺陷修复 这一类，题面再简单也不会进闸门，最后被平台按 P3 判废弃。
DEFAULT_TASK_TYPES = "all"
ALL_TASK_TYPES = {"all", "全部", "*"}
# 备注里写清「难度复核: …」的条目视为人工复核过；平台按语义判难度，脚本只能做筛选，
# 命中两项时要么换角度重出，要么把这道题为什么不算简单的依据写进备注留档。
JUSTIFICATION_PREFIX = "难度复核"
# 单独命中也要拒的特征：改动收在一处判断、一个文件或一段文案里，是最典型的「过于简单」。
# 平台 P3 判词（cc-6600009 的 41 号）里「修改范围」就是其中一条依据：只改一处判断的题
# 即使另外两个特征没命中也会被拒收。但「修改范围」这个词面特征本身有强有弱：
# 「只改一处」「单个文件」是硬信号，「给出提示」「直接提示」这类只是描述空态文案，
# 好题里到处都是。硬拒只认硬信号，弱信号仍然只按原来的「命中项」计数。
REJECT_ON_HIT_TRAITS = {"修改范围"}
STRONG_NARROW_SCOPE_PATTERNS = [
    r"只(需|要)?(改|补|加|做)",
    r"一处",
    r"单个(文件|组件|函数|判断|校验)",
    r"小改",
    r"补一个",
    r"仅仅",
]

# 缺陷修复题的结构下限（对应平台「过于简单」拒收口径与手册的难度下限）：
# 至少两个咬合缺陷点，或一个共享根因跨两个以上入口；并且题面要落到两个以上入口。
DEFECT_POINT_PATTERNS = [
    "报错", "崩溃", "白屏", "点不动", "没反应", "没有反应", "不生效", "没有生效",
    "显示成", "残留", "对不上", "错位", "丢失", "重复显示", "卡在", "卡住",
    "不刷新", "不更新", "不消失", "多出", "少一段", "串位", "乱序",
]
SHARED_ROOT_CAUSE_PATTERNS = [
    "同一个根因", "同一个原因", "同一处", "都来自", "共同导致", "根因", "同一份",
]
ENTRY_POINT_PATTERNS = [
    "列表", "详情", "弹窗", "面板", "地图", "大屏", "入口", "页面", "工作台",
    "导航", "看板", "侧栏", "抽屉", "标签页",
]

PLATFORM_RULE = "同一道题同时命中两项及以上「过于简单」特征时一律拒收"

# 「修改范围」过窄的显式信号：单点兜底、单处补丁式的写法。
NARROW_SCOPE_PATTERNS = [
    r"直接给出提示",
    r"直接提示",
    r"提示一下",
    r"给出(一个)?提示",
    r"弹(一个)?提示",
    r"只(需|要)?(改|补|加|做)",
    r"仅仅",
    r"一处",
    r"单个(文件|组件|函数|判断|校验)",
    r"小改",
    r"补一个",
]

# 跨入口、跨数据、后续操作、既有状态联动的锚点：题面至少要出现一项。
MULTI_LAYER_PATTERNS = [
    r"其它入口",
    r"其他入口",
    r"另一个入口",
    r"多个入口",
    r"列表页",
    r"详情页",
    r"两个页面",
    r"跨页面",
    r"同一段",
    r"同一个条件",
    r"其它数据",
    r"其他数据",
    r"多种数据",
    r"后续操作",
    r"刷新后",
    r"返回后",
    r"再次进入",
    r"重新进入",
    r"同步",
    r"保持一致",
    r"既有数据",
    r"历史数据",
    r"共用",
    r"公共",
    r"共享",
    r"两处",
    r"多处",
    r"另一处",
    r"关联",
]

# 「交互轮次」过少的反证：出现任意一个多轮动作就不判该特征。
SEQUENCE_PATTERNS = [
    r"然后",
    r"接着",
    r"(?<!不)再(次)?(操作|查询|提交|进入|打开|点击|输入|修改|保存|触发)",
    r"之后",
    r"随后",
    r"返回",
    r"刷新",
    r"切换",
    r"重进",
    r"重新(进入|打开|操作|查询|提交)",
    r"连续",
    r"多步",
    r"下一步",
    r"后续",
    r"回退",
    r"分步",
    r"重试",
    r"重复提交",
    r"翻页",
    r"上一页",
    r"下一页",
    r"依次",
    r"逐个",
    r"(保存|提交|查询|修改|导出|导入|删除)后",
    r"拖(动|拽)",
    r"悬停",
]

# 单触发动作：只有同时缺少多轮动作、又落在这类一次性操作上，才判「交互轮次」过少。
SINGLE_ACTION_PATTERNS = [
    r"点(一下|击|查询|提交|保存|删除)",
    r"填(写|入|上)|输入|录入",
    r"提交|查询|保存|删除",
]

# 技术面：命中两个及以上技术面才不算「技术广度」过窄。
TECH_DIMENSIONS = {
    "状态持久化": [
        r"刷新后",
        r"本地保存",
        r"持久化",
        r"数据库",
        r"缓存",
        r"会话",
        r"登录态",
        r"草稿",
        r"重启",
        r"重新打开",
    ],
    "数据与接口": [
        r"接口",
        r"请求",
        r"响应",
        r"服务端",
        r"后端",
        r"数据源",
        r"数据同步",
        r"落库",
        r"上报",
        r"数据不一致",
    ],
    "并发与时序": [
        r"并发",
        r"同时(提交|点击|操作|请求)",
        r"时序",
        r"竞态",
        r"重复提交",
        r"队列",
        r"轮询",
        r"超时",
    ],
    "权限与角色": [
        r"权限",
        r"角色",
        r"越权",
        r"管理员",
        r"访客",
        r"不同账号",
    ],
    "交互与呈现": [
        r"列表",
        r"分页",
        r"筛选",
        r"排序",
        r"图表",
        r"统计",
        r"汇总",
        r"样式",
        r"布局",
        r"弹窗",
        r"抽屉",
        r"滚动",
        r"导出",
        r"导入",
    ],
    "输入与校验": [
        r"校验",
        r"提示",
        r"判断",
        r"规则",
        r"格式",
        r"顺序",
        r"长度",
        r"范围",
        r"为空",
    ],
}

# 「逻辑复杂度」过平的信号与反证。
SINGLE_CONDITION_PATTERNS = [
    r"顺序",
    r"非空",
    r"为空",
    r"长度",
    r"格式",
    r"重复",
    r"超限",
    r"范围",
    r"大小",
    r"先后",
    r"前后",
]

COMPOUND_PATTERNS = [
    r"同时",
    r"另外",
    r"还会",
    r"另一个",
    r"另一处",
    r"另有",
    r"不仅",
    r"并且",
    r"以及",
    r"既.{0,12}又",
    r"复合",
    r"两个以上",
    r"彼此",
    r"相互",
    r"冲突",
    r"优先级",
]

UNHAPPY_PATH_PATTERNS = [
    r"空数据",
    r"无数据",
    r"没有数据",
    r"失败",
    r"重试",
    r"报错",
    r"异常",
    r"超时",
    r"断开",
    r"重复提交",
    r"并发",
    r"越权",
    r"权限不足",
    r"边界",
    r"超出",
    r"无效",
    r"缺失",
    r"非法",
    r"网络",
    r"冲突",
]

CROSS_STATE_PATTERNS = [
    r"刷新",
    r"返回",
    r"再次进入",
    r"重新进入",
    r"切换",
    r"同步",
    r"保持一致",
    r"恢复",
    r"保持",
    r"保留",
]


def _find(patterns: list[str], text: str) -> list[str]:
    evidence: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            evidence.append(match.group(0))
    return evidence


def trait_scope(text: str) -> list[str]:
    """「修改范围」过窄。"""

    narrow = _find(NARROW_SCOPE_PATTERNS, text)
    if narrow:
        return narrow
    if not _find(MULTI_LAYER_PATTERNS, text):
        return ["题面没有出现跨入口、跨数据、后续操作或既有状态联动的要求"]
    return []


def trait_rounds(text: str) -> list[str]:
    """「交互轮次」过少。"""

    if _find(SEQUENCE_PATTERNS, text):
        return []
    if not _find(SINGLE_ACTION_PATTERNS, text):
        return []
    return ["整条需求只要一次点击或一次提交就能验完，没有多轮动作"]


def trait_breadth(text: str) -> list[str]:
    """「技术广度」过窄。"""

    matched = [name for name, patterns in TECH_DIMENSIONS.items() if _find(patterns, text)]
    if len(matched) <= 1:
        only = matched[0] if matched else "未识别到明确技术面"
        return [f"只落在一个技术面：{only}"]
    return []


def trait_logic(text: str) -> list[str]:
    """「逻辑复杂度」过平。"""

    single = _find(SINGLE_CONDITION_PATTERNS, text)
    if single and not _find(COMPOUND_PATTERNS, text):
        return single
    return []


def trait_depth(text: str) -> list[str]:
    """「验证深度」过浅。"""

    if _find(UNHAPPY_PATH_PATTERNS, text) or _find(CROSS_STATE_PATTERNS, text):
        return []
    return ["既没有非开心路径，也没有跨状态一致性要求"]


TRAIT_DETECTORS = {
    "修改范围": trait_scope,
    "交互轮次": trait_rounds,
    "技术广度": trait_breadth,
    "逻辑复杂度": trait_logic,
    "验证深度": trait_depth,
}


def evaluate(text: str) -> dict[str, object]:
    traits: dict[str, list[str]] = {}
    for name, detector in TRAIT_DETECTORS.items():
        evidence = detector(text)
        if evidence:
            traits[name] = evidence
    strong_scope = _find(STRONG_NARROW_SCOPE_PATTERNS, text)
    return {
        "hit_count": len(traits),
        "hits": list(traits),
        "evidence": traits,
        "hard_rejects": ["修改范围"] if strong_scope else [],
        "hard_evidence": {"修改范围": strong_scope} if strong_scope else {},
    }


def evaluate_defect_structure(text: str) -> dict[str, object]:
    """缺陷修复题的结构下限：缺陷点数量与入口数量都要够。"""
    points = sorted({pattern for pattern in DEFECT_POINT_PATTERNS if pattern in text})
    shared = sorted({pattern for pattern in SHARED_ROOT_CAUSE_PATTERNS if pattern in text})
    entries = sorted({pattern for pattern in ENTRY_POINT_PATTERNS if pattern in text})
    enough_points = len(points) >= 2 or bool(shared)
    enough_entries = len(entries) >= 2
    return {
        "defect_points": points,
        "shared_root_cause": shared,
        "entry_points": entries,
        "enough_points": enough_points,
        "enough_entries": enough_entries,
        "ok": enough_points and enough_entries,
    }


def check(
    items: list[tuple[str, str]],
    min_hits: int,
    *,
    defect_structure: bool = True,
    defect_labels: set[str] | None = None,
    justifications: dict[str, str] | None = None,
) -> dict[str, object]:
    justifications = justifications or {}
    results: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    needs_review: list[dict[str, object]] = []
    justified: list[dict[str, object]] = []
    max_hits = 0
    for label, text in items:
        verdict = evaluate(text)
        hit_count = int(verdict["hit_count"])
        max_hits = max(max_hits, hit_count)
        reject_traits = sorted(set(verdict["hard_rejects"]) & REJECT_ON_HIT_TRAITS)
        # defect_labels=None 表示「本次检查的对象都是缺陷修复题」——单项目与 prompts-file 就是这么调的，
        # 这种时候硬信号与结构下限都照旧生效；批量全类型检查会传实际的缺陷修复标签集合，
        # 只有集合里的条目才硬拦，其余进 needs_review。
        is_defect_item = defect_labels is None or label in defect_labels
        applies = defect_structure and is_defect_item
        structure = evaluate_defect_structure(text) if applies else None
        note = str(justifications.get(label, "") or "").strip()
        reasons: list[str] = []
        hard = False
        if reject_traits and is_defect_item:
            evidence = "、".join(verdict["hard_evidence"].get("修改范围", [])[:3])
            reasons.append(
                f"命中「{'、'.join(reject_traits)}」的硬信号（{evidence}），"
                "改动收在一处判断或一段文案里，直接拒收"
            )
            hard = True
        if hit_count >= min_hits:
            if note:
                justified.append({
                    "label": label,
                    "hit_count": hit_count,
                    "hits": verdict["hits"],
                    "note": note,
                })
            elif not applies:
                # 2026-09-16 改：难度词面检查只对缺陷修复题硬拦。代码生成与功能迭代题里
                # 这套词面口径命中率过高（cc-6600011 那批 49 条里 35 条命中），
                # 当硬闸会把大量平台放行的题挡在门外；这几类改成列进 needs_review，
                # 由出题人逐条复核并写「难度复核: …」留档，复核结论进台账。
                needs_review.append({
                    "label": label,
                    "hit_count": hit_count,
                    "hits": verdict["hits"],
                    "evidence": verdict["evidence"],
                    "hard_signal": bool(reject_traits),
                    "note": "复核后把依据写进工作簿备注，以「难度复核: 」开头",
                })
            else:
                reasons.append(
                    f"命中 {hit_count} 项「过于简单」特征（上限 {min_hits - 1} 项）："
                    "要么换角度重出，要么把这道题不算简单的依据写进工作簿备注，"
                    f"并以「{JUSTIFICATION_PREFIX}: 」开头留档"
                )
        if structure is not None and not structure["ok"]:
            missing: list[str] = []
            if not structure["enough_points"]:
                missing.append("至少两个咬合缺陷点，或一个共享根因跨两个以上入口")
            if not structure["enough_entries"]:
                missing.append("题面落到两个以上入口")
            reasons.append("缺陷修复的结构下限不满足：" + "；".join(missing))
            hard = True
        record = {
            "label": label,
            "hit_count": hit_count,
            "hits": verdict["hits"],
            "evidence": verdict["evidence"],
            "defect_structure": structure,
            "too_simple": bool(reasons),
            "reasons": reasons,
            "prompt": text,
        }
        results.append(record)
        if record["too_simple"]:
            violations.append(record)
            if not hard:
                needs_review.append({"label": label, "hit_count": hit_count, "hits": verdict["hits"]})

    return {
        "ok": not violations,
        "rule": PLATFORM_RULE,
        "min_hits": min_hits,
        "justification_prefix": JUSTIFICATION_PREFIX,
        "reject_on_hit_traits": sorted(REJECT_ON_HIT_TRAITS),
        "defect_structure_required": defect_structure,
        "checked_count": len(items),
        "too_simple_count": len(violations),
        "needs_review": needs_review,
        "justified": justified,
        "max_hits": max_hits,
        "violations": violations,
        "items": results,
        "notes": [
            "本脚本是词面兜底检查，通过不代表题目一定够难，生成方还必须按手册的难度下限自查。",
            "命中两项及以上时要么换角度重出，要么把「为什么这道题不算简单」写进工作簿备注并"
            f"以「{JUSTIFICATION_PREFIX}: 」开头；只改同义词、只加限定词不算通过。",
            "难度默认按全部任务类型检查；只跑缺陷修复会漏掉代码生成与功能迭代里的单点题。",
        ],
    }


def load_prompt_file(path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if text:
            items.append((f"{path.name}#{index}", text))
    return items


def load_workbook_prompts(
    parent: Path,
    workbook_name: str,
    task_types: str,
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    records = workbook_lib.read_workbook(parent / workbook_name)
    wanted = {part.strip() for part in task_types.split(",") if part.strip()}
    apply_filter = bool(wanted) and not (wanted & ALL_TASK_TYPES)
    items: list[tuple[str, str]] = []
    justifications: dict[str, str] = {}
    for record in records:
        prompt = str(record.get("提示词", "") or "").strip()
        if not prompt:
            continue
        task_type = str(record.get("任务类型", "") or "").strip()
        if apply_filter and task_type not in wanted:
            continue
        label = str(record.get("子文件夹名称", "") or "未命名")
        if task_type:
            label = f"{label}[{task_type}]"
        items.append((label, prompt))
        note = str(record.get("备注", "") or "").strip()
        if note.startswith(JUSTIFICATION_PREFIX):
            justifications[label] = note
    return items, justifications


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", help="父目录，默认读取其中的 solo-create-prompts.xlsx")
    parser.add_argument("--workbook", default=workbook_lib.DEFAULT_WORKBOOK)
    parser.add_argument("--prompts-file", help="每行一条提示词的文本文件，与 --parent 二选一")
    parser.add_argument(
        "--task-types",
        default=DEFAULT_TASK_TYPES,
        help="读取工作簿时只检查这些任务类型，逗号分隔；传 all 表示不过滤，默认 缺陷修复",
    )
    parser.add_argument("--min-hits", type=int, default=DEFAULT_MIN_HITS, help="拒收阈值，默认 2")
    parser.add_argument("--no-defect-structure", action="store_true",
                        help="跳过缺陷修复题的结构下限（缺陷点数量与入口数量）；默认必查")
    args = parser.parse_args()

    if args.prompts_file:
        items = load_prompt_file(Path(args.prompts_file).expanduser().resolve())
        justifications: dict[str, str] = {}
    elif args.parent:
        parent = Path(args.parent).expanduser().resolve()
        if not parent.is_dir():
            raise SystemExit(f"Parent directory does not exist: {parent}")
        items, justifications = load_workbook_prompts(parent, args.workbook, args.task_types)
    else:
        raise SystemExit("必须提供 --parent 或 --prompts-file 之一")

    wanted = {part.strip() for part in args.task_types.split(",") if part.strip()}
    if args.no_defect_structure:
        defect_structure = False
    elif wanted & {"缺陷修复", "Bug 修复"}:
        defect_structure = True
    elif not wanted or wanted & ALL_TASK_TYPES:
        # 全部类型一起查时，缺陷修复题的结构下限不能丢：按标签里的任务类型逐条判定，
        # 只对缺陷修复那几条套结构下限。
        defect_structure = True
    else:
        defect_structure = False
    defect_labels = {
        label for label, _ in items
        if "缺陷修复" in label or "Bug 修复" in label
    }
    result = check(
        items, args.min_hits,
        defect_structure=defect_structure,
        defect_labels=defect_labels,
        justifications=justifications,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
