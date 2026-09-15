#!/usr/bin/env python3
"""检查一批已生成提示词之间的重复、语义近似和复合需求交叉重叠。

三项判据（默认阈值都是 20%）：

1. `text_overlap` 文字重复率：字符 3-gram 集合的重合率，口径是
   `|A ∩ B| / min(|A|, |B|)`，对"同一套句架反复换皮"这种写法最敏感。
2. `semantic_similarity` 语义近似度：在本批提示词上做字符 2-gram 的 TF-IDF 余弦。
   这是词面语义近似，不是真正的向量语义；生成方还必须自己做一次语义复核，
   脚本结果是客观兜底，两边取更严的那个。
3. `capability_overlap` 复合需求交叉重叠：按业务能力词典从提示词里抽取能力点，
   两条提示词共享 2 个以上业务能力点时判为交叉重叠。通用约束类标签
   （空态、失败重试、刷新保持等）不计入交叉，否则每条都必须带非开心路径会让
  这个判据失去区分度。
   出现率超过 30% 的能力标签视为本批通用能力（例如一个批次里近半数提示词都带
   "导出"），只记录不判违规，避免用高频词制造假交叉。

用法：

    python3 scripts/check_prompt_dedup.py --parent "<父目录>"
    python3 scripts/check_prompt_dedup.py --parent "<父目录>" --include-history
    python3 scripts/check_prompt_dedup.py --prompts-file <每行一条提示词的文本文件>

命中任一判据时返回 `ok: false` 并以退出码 1 结束，便于流程里直接拦停。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_prompt_workbook as workbook_lib  # noqa: E402
from prompt_history_lib import detect_prefix, load_history  # noqa: E402


DEFAULT_THRESHOLD = 0.2
GENERIC_CAPABILITY_RATIO = 0.3
GENERIC_CAPABILITY_MIN_COUNT = 3

# 业务能力词典：只有这些标签参与复合需求交叉重叠判据。
CAPABILITY_LEXICON = {
    "筛选": ("筛选", "过滤"),
    "排序": ("排序", "排列顺序"),
    "搜索": ("搜索", "检索", "查找", "查询"),
    "分页": ("分页", "翻页", "页码"),
    "导出": ("导出", "下载文件", "生成文件"),
    "导入": ("导入", "上传", "粘贴"),
    "统计汇总": ("统计", "汇总", "占比", "合计"),
    "图表可视化": ("图表", "曲线", "热力图", "可视化", "画布"),
    "权限角色": ("权限", "角色", "管理员", "访客", "登录"),
    "通知提醒": ("通知", "提醒", "消息推送"),
    "审核审批": ("审核", "审批", "待审"),
    "批量操作": ("批量", "多选", "框选", "全选"),
    "主题外观": ("主题", "配色", "深浅", "外观"),
    "快捷键": ("快捷键", "键盘", "回车键", "方向键"),
    "多语言": ("多语言", "中英", "语言切换", "翻译"),
    "动画播放": ("动画", "播放", "过渡效果"),
    "分享协作": ("分享", "协作", "协同"),
    "历史记录": ("历史记录", "过往记录", "最近使用"),
    "打印": ("打印", "纸质"),
}

# 通用约束类标签：只用于提示，不参与交叉重叠判据。
GENERIC_LEXICON = {
    "空态": ("空态", "无数据", "没有数据"),
    "失败重试": ("失败", "重试", "报错"),
    "刷新保持": ("刷新", "保持", "恢复", "保留"),
}

PUNCTUATION = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】<>《》\-—_/\\|~`·…]")


def normalize(text: str) -> str:
    return PUNCTUATION.sub("", text)


def ngrams(text: str, size: int) -> set[str]:
    normalized = normalize(text)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def text_overlap(left: str, right: str, size: int = 3) -> float:
    a, b = ngrams(left, size), ngrams(right, size)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def tfidf_vectors(texts: list[str], size: int = 2) -> list[dict[str, float]]:
    counters = [Counter(ngrams(text, size)) for text in texts]
    document_frequency: Counter[str] = Counter()
    for counter in counters:
        document_frequency.update(counter.keys())
    total = len(counters)
    vectors: list[dict[str, float]] = []
    for counter in counters:
        vector = {
            gram: (1 + math.log(count)) * math.log((total + 1) / (document_frequency[gram] + 1))
            for gram, count in counter.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({gram: value / norm for gram, value in vector.items()})
    return vectors


def semantic_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(gram, 0.0) for gram, value in left.items())


def capabilities(text: str) -> set[str]:
    found = set()
    for label, keywords in CAPABILITY_LEXICON.items():
        if any(keyword in text for keyword in keywords):
            found.add(label)
    return found


def generic_constraints(text: str) -> set[str]:
    found = set()
    for label, keywords in GENERIC_LEXICON.items():
        if any(keyword in text for keyword in keywords):
            found.add(label)
    return found


def batch_generic_capabilities(capability_sets: list[set[str]]) -> set[str]:
    """出现率过高的能力标签在本批里没有区分度，不参与交叉重叠判据。"""

    if not capability_sets:
        return set()
    counts: Counter[str] = Counter()
    for item in capability_sets:
        counts.update(item)
    total = len(capability_sets)
    return {
        label
        for label, count in counts.items()
        if count >= GENERIC_CAPABILITY_MIN_COUNT and count / total > GENERIC_CAPABILITY_RATIO
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
        if prompt:
            items.append((str(record.get("子文件夹名称", "") or "未命名"), prompt))
    return items


def load_history_prompts(
    items: list[tuple[str, str]],
    parent: Path | None = None,
) -> list[tuple[str, str]]:
    """取同项目的历史提示词。

    有 `parent` 时按 `project_path` 精确匹配：concat 命名下每条的编号前缀都不相同
    （`016421`、`016422`、`0164210`…），按前缀匹配会漏掉同批其它目录的历史。
    没有 `parent` 时才退回按前缀匹配。
    """

    current_prompts = {prompt.strip() for _, prompt in items}
    project_paths = {str(parent / label) for label, _ in items} if parent else set()
    prefixes = {detect_prefix(label) for label, _ in items}
    prefixes.discard("")
    if not project_paths and not prefixes:
        return []
    history: list[tuple[str, str]] = []
    for record in load_history():
        record_path = str(record.get("project_path", "") or "")
        record_prefix = str(record.get("prefix", "") or "")
        if project_paths:
            if record_path not in project_paths:
                continue
        elif record_prefix not in prefixes:
            continue
        prompt = str(record.get("prompt", "") or "").strip()
        # 历史里本来就有本批刚记录的同一批提示词，自身不算重复。
        if prompt and prompt not in current_prompts:
            history.append((f"历史:{record.get('project_name', '')}", prompt))
    return history


def check(
    items: list[tuple[str, str]],
    threshold: float,
) -> dict[str, object]:
    texts = [prompt for _, prompt in items]
    vectors = tfidf_vectors(texts)
    capability_sets = [capabilities(prompt) for prompt in texts]
    generic_capabilities = batch_generic_capabilities(capability_sets)

    violations: list[dict[str, object]] = []
    max_text = 0.0
    max_semantic = 0.0
    pair_count = 0

    for left, right in itertools.combinations(range(len(items)), 2):
        pair_count += 1
        left_label, left_prompt = items[left]
        right_label, right_prompt = items[right]
        overlap = text_overlap(left_prompt, right_prompt)
        semantic = semantic_similarity(vectors[left], vectors[right])
        shared_all = capability_sets[left] & capability_sets[right]
        shared = sorted(shared_all - generic_capabilities)
        shared_ignored = sorted(shared_all & generic_capabilities)
        max_text = max(max_text, overlap)
        max_semantic = max(max_semantic, semantic)

        if overlap > threshold:
            violations.append(
                {
                    "kind": "text_overlap",
                    "score": round(overlap, 4),
                    "left": left_label,
                    "right": right_label,
                    "left_prompt": left_prompt,
                    "right_prompt": right_prompt,
                }
            )
        if semantic > threshold:
            violations.append(
                {
                    "kind": "semantic_similarity",
                    "score": round(semantic, 4),
                    "left": left_label,
                    "right": right_label,
                    "left_prompt": left_prompt,
                    "right_prompt": right_prompt,
                }
            )
        if len(shared) >= 2:
            violations.append(
                {
                    "kind": "capability_overlap",
                    "score": len(shared),
                    "shared_capabilities": shared,
                    "ignored_generic_capabilities": shared_ignored,
                    "left": left_label,
                    "right": right_label,
                    "left_prompt": left_prompt,
                    "right_prompt": right_prompt,
                }
            )

    return {
        "ok": not violations,
        "threshold": threshold,
        "checked_count": len(items),
        "pairs_checked": pair_count,
        "max_text_overlap": round(max_text, 4),
        "max_semantic_similarity": round(max_semantic, 4),
        "generic_capabilities": sorted(generic_capabilities),
        "violation_count": len(violations),
        "violations": violations,
        "notes": [
            "语义近似度按字符二元组 TF-IDF 余弦计算，属于词面语义指标，生成方仍需做一次语义复核。",
            "复合需求交叉重叠只看业务能力点，空态、失败重试、刷新保持这类通用约束不参与判据。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", help="父目录，默认读取其中的 solo-create-prompts.xlsx")
    parser.add_argument("--workbook", default=workbook_lib.DEFAULT_WORKBOOK)
    parser.add_argument("--prompts-file", help="每行一条提示词的文本文件，与 --parent 二选一")
    parser.add_argument("--include-history", action="store_true", help="同时与同前缀的历史提示词比对")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="判据阈值，默认 0.2")
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

    if args.include_history:
        items = items + load_history_prompts(items, parent)

    result = check(items, args.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
