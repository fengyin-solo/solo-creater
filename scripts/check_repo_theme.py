#!/usr/bin/env python3
"""同仓库题目语义去重（平台查重规则 C 的本地预检）。

平台规则 C 判的是「主体 + 需求模式」，不看措辞，也不看这个模块在仓库里存不存在。
2026-09-16 把平台上 97 条提交记录、41 条废弃（其中规则 C 38 条）的判词全部拉下来复盘，
口径如下：

- **字面相似度不作数**：判废对里最低的只有 9.5%，平台原话「只改写措辞无效」；
- **同主体 + 同模式就算废**，哪怕功能点完全不同：「同属设备监控平台按设备统计一段时间
  采样的分析视图，一个看位置停留热区、一个看电量下降速度」「共同主体是生成受控只读访问
  入口，但一为现场扫码巡检登记、一为监控视图只读分享」「同为围栏模块的属性扩展，功能点
  不同」；
- **0-1 代码生成不豁免**：cc-6600009 那批 17 条代码生成废了 10 条（59%），比功能迭代还高，
  21 对判废对里 10 对两边都是 0-1 代码生成。凭空造的功能只要挂在同一个主体或用了同一个
  需求模式，照样作废；
- **跨模块也生效**：判废对里 29%（cc-6600009）到 59%（cc-6600011）是跨模块的同模式对。

这个脚本把上面那套口径做成三层预检（数字都是拿 38 条真实判废对回测出来的）：

1. **硬拦**：台账缺失 / 主体识别为空 / 单仓库条数超 `--max-per-repo`（默认 35）/
   **同主体 + 同需求模式重复** / 同主体超 `--max-per-subject`（默认 2）/
   单模式占比超 `--max-mode-ratio`（默认 0.20）/ 整批句式片段超 `--max-ngram-ratio`
   （默认 0.20）/ 新增能力类占比超 `--max-new-capability-ratio`（默认 0.50）。
   这一层对真实判废对的召回约 35%–52%——**关键词复刻不了平台的语义判据，硬拦不是保证**。
2. **必复核清单**（`review_list` + `candidate_pairs`）：同主体 或 需求模式有交集就进清单，
   这一层对真实判废对的召回 95%–100%。SKILL 要求逐条写结论，不签名不许提交。
3. **回归门**：`--judgement-corpus` 指向 `tests/fixtures/rule-c-corpus.json` 时，
   脚本对语料里的判废对实测召回，低于 `--min-candidate-recall`（默认 0.90）直接判不通过。
   判据改动过不了这道门就不许写表。

单仓库条数上限的意义：平台是在同一个仓库里两两比的，铺得越多最近邻越近。历史两批
48/49 条在一个仓库里，废弃 44%/35%；上限压到 35 条并配上面三层，才有机会进个位数。

用法：

    python3 scripts/check_repo_theme.py --parent "<父目录>"
    python3 scripts/check_repo_theme.py --parent "<父目录>" --write-ledger
    python3 scripts/check_repo_theme.py --parent "<父目录>" --repo "<origin>" \
        --judgement-corpus tests/fixtures/rule-c-corpus.json
    python3 scripts/check_repo_theme.py --prompts-file <每行一条提示词的文本文件> --ledger <父目录>/repo-theme-ledger.json

`--write-ledger` 会把每条的主体、需求模式与能力类型写进父目录的 `repo-theme-ledger.json`，
后续批次继续在同一份台账上比，做到「同仓库全局去重」而不是只看当前这一批。

命中任一判据时返回 `ok: false` 并以退出码 1 结束。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_prompt_workbook as workbook_lib  # noqa: E402


LEDGER_FILENAME = "repo-theme-ledger.json"
FEATURE_POINTS_FILENAME = "repo-feature-points.json"
CAPACITY_FILENAME = "repo-capacity.json"
MANIFEST_FILENAME = "prompt-generation-manifest.json"
DEFAULT_MAX_PER_MODULE = 3
# 单仓库条数上限：平台是在同一个仓库里两两比，铺得越多最近邻越近。
# 2026-09-16 复盘两个历史批次（48/49 条同仓库，废弃 44%/35%）后定的护栏。
DEFAULT_MAX_PER_REPO = 35
# 同一个主体最多出几条。2026-09-16 复标定：**默认 2 条，但两条必须跨任务家族**。
#
# 过程：cc-9900003 那批 20 条按「每主体 2 条、不限家族」生成，闸门判 ok 而人工复核读出 5 对中高风险
# ——10 个主体必然产出 9 对同主体近邻，其中同家族的两条（都是"新增能力"）最像。
# 拿 38 对真实判废对算每对判废率：同主体对 4.7%–7.7%、跨主体对 0.85%–1.02%。
# 再按任务家族拆开看：
#   · 同家族同主体被判废：代码生成↔代码生成 14 对、代码生成↔功能迭代 11 对、
#     功能迭代↔功能迭代 10 对、缺陷修复↔缺陷修复 3 对；
#   · **「新增能力类 ↔ 缺陷修复」这种跨家族组合：0 对**（38 对里一对都没有）。
# 所以放开到每主体 2 条时，硬性要求两条来自不同任务家族；同家族的第二条一律拦下。
# 另外两道护栏继续生效：同主体同对象（对象实词重合 ≥2）拦、句式指纹拦。
# 想要零风险就显式收回到 1 条/主体 + 1 条/模式（`--max-per-subject 1 --max-per-mode 1`）。
DEFAULT_MAX_PER_SUBJECT = 2
# 同一个需求模式最多出几条，默认 **2 条**（配合每主体 2 条）。这条是硬拦，不是比例护栏。
#
# 2026-09-16 按 38 对真实判废对做集合运算：判废对里 22 对同主体、15 对跨主体但同模式，
# 只剩 1 对两者都不沾。按当年的批次（49 条塞进 9 个模块）算，同模式对是最小的一类风险
# （1.1%/对）；放开到每模式 2 条，一批 22 条也只有 9 对同模式对，预期废弃约 0.1 条。
#
# 产能 = min(单仓库上限, 主体数 × 每主体上限, 模式数 × 每模式上限)：
# 默认 2/2 时，cc-9900003 那种 11 个主体、13 类模式的仓库是 min(35, 22, 26) = 22 条；
# 收到 1/1 就是 11 条（零风险口径）。
DEFAULT_MAX_PER_MODE = 2
# 任务家族：同主体两条必须跨家族。依据是判废对里跨「新增能力类 ↔ 缺陷修复」的组合 0/38。
TASK_FAMILY = {
    "代码生成": "新增能力",
    "功能迭代": "新增能力",
    "缺陷修复": "缺陷修复",
    "Bug修复": "缺陷修复",
    "Bug 修复": "缺陷修复",
    "代码重构": "结构改造",
    "代码理解": "结构改造",
    "工程化": "结构改造",
    "代码测试": "结构改造",
}
# 单个需求模式占整批的比例上限：跨模块同模式的判废对占 29%–59%，要把模式铺开。
DEFAULT_MAX_MODE_RATIO = 0.20
# 整批句式指纹上限：优化后的 cc-9900003 里「刷新后与返回后」出现在 74% 的题面里，
# 老批次最高才 10%。判词里「约束句式一致」是明确的判废理由。
DEFAULT_MAX_NGRAM_RATIO = 0.20
NGRAM_SIZE = 6
# 新增能力类（0-1 代码生成 + 功能迭代）占比上限：历史实测这两类废弃 43%–57%，
# 缺陷修复 14%–29%，重构/理解/工程化 0%。把配比压下来比改措辞有用得多。
DEFAULT_MAX_NEW_CAPABILITY_RATIO = 0.50
NEW_CAPABILITY_TYPES = {"代码生成", "功能迭代"}
# 最近邻复核清单每条题列几个邻居
DEFAULT_REVIEW_NEIGHBORS = 3
# 同主体两条题的业务对象实词最多能重合几个：重合达到这个数就判「同主体同对象」，换主体。
# 拿 38 对真实判废对校准：这条把硬拦召回从 52%/35% 提到 57%/59%，
# 且正好覆盖 cc-9900003 那批人工读出的全部高危对（导航×登录态、死链×检测中断、
# 导入×重复条目、分类×重名、账号×切换残留）。
DEFAULT_MIN_SHARED_OBJECT_WORDS = 2
# 判据对真实判废对的召回下限：拿 38 对判废对回测，现在这套口径（同主体或同模式即拦）
# 是 97.4%，所以下限收到 0.95。低于这个数说明判据被改坏了，不许写表。
DEFAULT_MIN_CANDIDATE_RECALL = 0.95
# 真实判词语料：平台上被判规则 C 的题面与判废对，随 skill 一起走。
DEFAULT_CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rule-c-corpus.json"
# 闸门版本：写进台账与生成清单，跨机器一眼能看出这批题是不是按新版规则出的。
GATE_VERSION = "2026-09-16-subject-mode-unique"
# 同仓库跨批次台账：按仓库归集，不跟着父目录走，避免换一个父目录就重新开始算配额。
# 需要隔离（例如测试、或想放到共享盘）时用 SOLO_CREATE_REPO_LEDGER_ROOT 覆盖。
REPO_LEDGER_ROOT = Path(
    os.environ.get("SOLO_CREATE_REPO_LEDGER_ROOT", "~/.codex/repo-theme-ledgers")
).expanduser()
# 同模块内「功能点近似」的判据：能力短语里实词重合的数量。
# 2026-09-16 实测（cc-6600011 那批）：这条只能当**候选提示**，不能当硬拦——
# 中文按 2/3 字片段切出来的重合里混着「增录 / 希望支」这类拼出来的碎片，
# 拿它硬拦会把 18 对（其中多数是不同功能点）全挡住。硬拦仍由
# 「台账缺失、主体/模式识别为空、同主体超过 1 条、同模式超过 1 条、同主体同对象、
# 单仓库超容量、句式指纹、新增能力类占比」这几条负责（2026-09-16 复标定后）。
FEATURE_NEAR_CANDIDATE = 3
GENERIC_CAPABILITY_WORDS = {
    "目前", "现在", "已有", "原有", "希望", "增加", "新增", "功能", "能力", "可以", "支持",
    "用户", "系统", "界面", "列表", "数据", "时候", "之后", "以及", "并且", "同时", "这个",
    "那个", "一个", "把", "在", "与", "和", "了", "上", "下", "里", "按", "能", "也", "都",
}

# 从仓库源码派生模块词典时用到的中文串；这些词在每个组件里都会出现，不能拿来定模块。
GENERIC_TOKENS = {
    "用户", "新增", "支持", "显示", "状态", "数据", "界面", "功能", "面板", "列表",
    "提示", "结果", "内容", "信息", "时间", "时刻", "切换", "保存", "说明", "记录",
    "操作", "查看", "原有", "保持", "默认", "边界", "失败", "重试", "异常", "空态",
    "现在", "希望", "需要", "可以", "不能", "如果", "以及", "并且", "同时", "之后",
}
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
REPO_SOURCE_PATTERNS = (
    "frontend/src/**/*.tsx",
    "frontend/src/**/*.ts",
    "frontend/src/**/*.vue",
    "frontend/src/**/*.jsx",
    "backend/app/**/*.py",
    "src/**/*.ts",
    "src/**/*.tsx",
    "app/**/*.py",
)


def derive_repo_modules(repo: Path, *, min_tokens: int = 3) -> dict[str, set[str]]:
    """从仓库源码派生模块词典：一个组件或服务文件就是一个模块，关键词取该文件里的中文串。

    2026-09-16 按 cc-6600011 那批的实测补：原来只靠一份通用模块词表（告警中心、围栏工作台、
    地图与图层这些），换一个技术栈就全落空，49 条题面里只认出 3 条模块、1 对候选，
    闸门等于没开，最后 17 条被平台按规则 C 判废弃。改成从仓库自身结构派生之后，
    「同一个组件的功能点反复出题」这种事才数得出来。
    """
    modules: dict[str, set[str]] = {}
    if not repo or not Path(repo).is_dir():
        return modules
    root = Path(repo)
    for pattern in REPO_SOURCE_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if path.name.endswith(".d.ts") or path.name in {"main.ts", "main.tsx", "index.ts", "index.tsx"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tokens = {token for token in CJK_RUN_RE.findall(text) if token not in GENERIC_TOKENS}
            # 组件里的中文串常常比题面长（开始录制 / 历史录制 对 录制），所以把长词再切成
            # 二字片段一起入词典，"录制" 这类题面常用词才有机会命中对应模块。
            fragments: set[str] = set()
            for token in list(tokens):
                for index in range(len(token) - 1):
                    piece = token[index:index + 2]
                    if piece not in GENERIC_TOKENS:
                        fragments.add(piece)
            tokens |= fragments
            if len(tokens) >= min_tokens:
                modules.setdefault(path.stem, set()).update(tokens)
    return modules


def detect_derived_module(text: str, derived: dict[str, set[str]], *, min_hits: int = 2) -> tuple[str, int]:
    """在派生模块里找主模块：要求命中词的总出现次数至少 2 次，避免一条题被一个泛词带走。

    2026-09-16 起按「出现次数」而不是「不同词个数」算：一条题里反复提到同一个核心词
    （例如「录制」出现三次）已经足够说明它改的是哪一块，不必强求两个不同关键词。
    够不到 2 次时再退一步：取命中最多的那个模块当**弱命中**（分数 1），
    避免「新增波形配色」这种只提一次模块名的题被判成模块未识别而整条写不进去。
    """
    best_name, best_score = "", 0
    weak_name, weak_score = "", 0
    for name, tokens in derived.items():
        hits = [token for token in tokens if token in text]
        if not hits:
            continue
        score = sum(text.count(token) for token in hits)
        if score < min_hits:
            # 弱命中取分数最高的那个，不能按遍历顺序取第一个，否则会把「波形」类的题
            # 归到字母序最前的组件上
            if score > weak_score:
                weak_name, weak_score = name, score
            continue
        if score > best_score:
            best_name, best_score = name, score
    if best_score:
        return best_name, best_score
    return weak_name, weak_score

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

# 需求模式：从 38 条规则 C 判词里归纳出来的一层。平台判废时说的「同属…新增能力」
# 「同属…的分析视图」「共同的受控只读访问入口」「同为…属性扩展」「同一种约束句式」
# 说的都是这一层，而不是具体功能。同一主体 + 同一模式是硬拦；模式有交集要进复核清单。
DEMAND_MODE_LEXICON: dict[str, tuple[str, ...]] = {
    "新增展示视图": ("视图", "展示", "列出", "呈现", "概览", "看板", "排列", "显示出来"),
    "规则与阈值": ("阈值", "口径", "判定", "规则", "上限", "范围", "条件", "标准", "不允许保存"),
    "状态流转": ("流转", "状态", "生命周期", "归档", "恢复", "回到", "切换", "变更"),
    "权限与归属": ("权限", "越权", "归属", "只读", "受控", "只能查看", "不能改动", "共享"),
    "批量操作": ("批量", "多条", "多选", "逐条", "一次提交", "逐台", "整组"),
    "导入导出": ("导入", "导出", "文件", "下载", "上传", "打包", "书签"),
    "列表定位与筛选": ("筛选", "排序", "翻页", "分页", "定位", "检索", "搜索", "查询", "过滤"),
    "持久化与一致性": ("刷新", "返回", "重新进入", "持久", "一致", "对得上", "保留原", "同步"),
    "边界与空态": ("空态", "暂无", "没有", "失败", "重试", "中断", "说明原因", "异常"),
    "缺陷处置": ("报错", "不生效", "残留", "点不动", "显示成", "对不上", "错位", "丢失", "重复显示"),
    # 原来这两类合成一个「重构与工程化」，会把两种平台任务类型（代码重构 / 工程化）算成同一个模式，
    # 导致同一批里「重构题」和「工程化题」互斥、只能留一条。平台自己的任务类型表就把它们分开，
    # 所以这里拆成两个模式；拆完 13 类模式，对 38 对判废对的召回不变（没有判废对是靠这一格配上的）。
    "代码重构收拢": ("收拢", "各写了一遍", "共用", "抽取", "写成一份", "统一写法"),
    "工程化与流程": ("构建", "上线", "流水线", "依赖", "环境", "数据库", "示例数据", "部署", "本地开发"),
    "代码理解": ("读懂", "理清", "梳理", "分步说明", "流程图", "链路", "经过哪些环节"),
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


def detect_modes(text: str) -> tuple[str, list[str]]:
    """需求模式：主模式（命中最多的那个）+ 全部有交集的模式。

    主模式用来判「同主体 + 同模式」；有交集的模式集合用来出复核清单，
    因为平台的判词是拿「同属…的分析视图」这类粗口径比对的。
    """
    scored = [(name, _score(text, keys)) for name, keys in DEMAND_MODE_LEXICON.items()]
    scored = [(name, score) for name, score in scored if score > 0]
    if not scored:
        return "", []
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[0][0], [name for name, _ in scored]


def cjk_ngrams(text: str, size: int = NGRAM_SIZE) -> set[str]:
    """题面里的纯汉字 n 字片段，用来数整批的句式指纹。"""
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for index in range(len(run) - size + 1):
            grams.add(run[index:index + size])
    return grams


def classify(label: str, text: str, derived: dict[str, set[str]] | None = None,
             *, is_repair: bool = False, exempt: bool = False) -> dict[str, object]:
    primary, secondary = detect_modules(text)
    if derived:
        derived_name, derived_score = detect_derived_module(text, derived)
        if derived_name:
            primary = primary or derived_name
            if derived_name not in secondary:
                secondary.append(derived_name)
    mode, modes = detect_modes(text)
    return {
        "label": label,
        "module": primary,
        "modules": ([primary] if primary else []) + secondary,
        "capabilities": detect_capabilities(text),
        "skeletons": detect_skeletons(text),
        "mode": mode,
        "modes": modes,
        "subject_mode": f"{primary}|{mode}" if primary and mode else "",
        "feature_point": feature_point(primary, detect_capabilities(text)),
        "capability_words": sorted(capability_content_words(text)),
        "object_words": sorted(object_content_words(text)),
        "ngrams": sorted(cjk_ngrams(text)),
        "repair_round": is_repair,
        "module_exempt": exempt,
    }


def capability_phrase(text: str) -> str:
    """题面里的能力短语：从「新增 / 希望增加 / 希望它能…」起，到冒号或 24 字为止。"""
    match = re.search(r"(?:新增|希望增加|希望它能|希望改成|希望)[^：:]{0,24}", text or "")
    return match.group(0) if match else (text or "")[:24]


def capability_content_words(text: str) -> set[str]:
    """能力短语里的实词（2 字与 3 字片段，去掉泛词），用来判断两条题是不是同一个功能点。

    2026-09-16 按 cc-6600011 的判词标定：同模块里「能力短语实词重合 >= 2」的对共 20 对，
    其中 16 对含平台判废弃的记录（约八成）；重合 >= 3 的 13 对里 10 对含废弃。
    所以重合 >= 3 直接硬拦，重合 2 进候选清单。
    """
    words: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", capability_phrase(text)):
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                word = run[index:index + size]
                if word not in GENERIC_CAPABILITY_WORDS:
                    words.add(word)
    return words


MODE_KEYWORDS: set[str] = (
    {key for keys in DEMAND_MODE_LEXICON.values() for key in keys}
    | {key for keys in CAPABILITY_LEXICON.values() for key in keys}
    | {key for keys in SKELETON_LEXICON.values() for key in keys}
)


def object_content_words(text: str) -> set[str]:
    """题面里的**业务对象实词**：去掉需求模式词、能力词与泛词之后剩下的名词片段。

    这是判据的第三根轴。2026-09-16 复盘发现，只锁「主体 + 需求模式」是不够的：
    同一个主体一旦出第二条，就多出一对近邻；只要这两条落在同一个业务对象上
    （导航 × 登录态、分类 × 重名、导入 × 重复条目、账号 × 切换残留），平台照样按
    「同为 X 模块的属性扩展」判废，而模式标签不同恰恰让它躲过了前两根轴。

    拿 38 对真实判废对校准：加上「同主体 + 共享对象实词 ≥ 2」这一条，硬拦召回从
    52%/35% 提到 57%/59%，而这条正好把这批 20 条里我人工读出的 5 对高危全部拦下。
    """
    words: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                word = run[index:index + size]
                if (word in GENERIC_TOKENS or word in GENERIC_CAPABILITY_WORDS
                        or word in MODE_KEYWORDS):
                    continue
                words.add(word)
    return words


def feature_point(module: str, capabilities: list[str]) -> str:
    """功能点 = 模块 + 能力大类；同一个功能点只允许出 1 条主任务。

    模块配额只能防「同一个模块出太多」，防不住「同一个模块里把同一件事换个说法再出一遍」——
    cc-6600011 那批 17 条规则 C 废弃里，多数就是同模块同能力的两条题（列表翻页对列表过滤、
    占比口径对参考范围、走势曲线对越线预警）。
    """
    if not module or not capabilities:
        return ""
    return f"{module}|{sorted(capabilities)[0]}"


def pair_features(a: dict, b: dict, text_a: str, text_b: str) -> dict[str, object]:
    """两条题的相似特征：主体、需求模式、能力大类、能力短语实词、正文字符相似度。

    平台判词是语义级的，这里只做**可数的近似**：实测「同主体 或 模式有交集」这组口径
    对 38 条真实判废对能覆盖 95%–100%（cc-6600009 20/21，cc-6600011 17/17），
    所以它当复核清单的判据；而「同主体 + 同模式」只有 35%–52%，只当硬拦。
    """
    modules = set(a["modules"]) & set(b["modules"])
    modes = set(a["modes"]) & set(b["modes"])
    capabilities = set(a["capabilities"]) & set(b["capabilities"])
    skeletons = set(a["skeletons"]) & set(b["skeletons"])
    words = set(a["capability_words"]) & set(b["capability_words"])
    same_subject = bool(a["module"] and a["module"] == b["module"])
    structure = 0.0
    if same_subject:
        structure += 0.55
    if modes:
        structure += 0.25 * min(1.0, len(modes) / 2.0)
    structure += 0.20 * min(1.0, len(words) / 3.0)
    try:
        import difflib

        text_score = difflib.SequenceMatcher(None, text_a, text_b).ratio()
    except Exception:  # noqa: BLE001 - difflib 不可能失败，兜底不影响主流程
        text_score = 0.0
    return {
        "modules": sorted(modules),
        "modes": sorted(modes),
        "capabilities": sorted(capabilities),
        "skeletons": sorted(skeletons),
        "shared_words": sorted(words),
        "same_subject": same_subject,
        "score": round(0.6 * min(1.0, structure) + 0.4 * text_score, 4),
    }


def build_review_list(records: list[dict], *, neighbors: int = DEFAULT_REVIEW_NEIGHBORS) -> list[dict]:
    """每条题按相似度列出最像的几条邻居，供人工逐条复核并写差异结论。"""
    review: list[dict] = []
    for record in records:
        scored: list[tuple[float, dict, dict]] = []
        for other in records:
            if other is record:
                continue
            features = pair_features(record, other, str(record.get("_text") or ""),
                                     str(other.get("_text") or ""))
            scored.append((float(features["score"]), other, features))
        scored.sort(key=lambda item: (-item[0], str(item[1]["label"])))
        picked = scored[:max(0, neighbors)]
        if not picked:
            continue
        review.append({
            "label": record["label"],
            "subject": record["module"],
            "mode": record["mode"],
            "subject_mode": record["subject_mode"],
            "neighbors": [
                {
                    "label": other["label"],
                    "subject": other["module"],
                    "mode": other["mode"],
                    "score": features["score"],
                    "same_subject": features["same_subject"],
                    "shared_modes": features["modes"],
                    "shared_words": features["shared_words"][:6],
                }
                for _, other, features in picked
            ],
        })
    return review


def suggest_type_mix(
    capacity: int,
    *,
    max_new_capability_ratio: float = DEFAULT_MAX_NEW_CAPABILITY_RATIO,
) -> dict[str, int]:
    """按容量给出任务类型配比：新增能力类不超过一半，其余给缺陷修复/重构/理解/工程化。

    依据是实测废弃率——新增能力类（代码生成 + 功能迭代）43%–57%，缺陷修复 14%–29%，
    重构/理解/工程化 0%。所以整批里有半数是「不需要凭空造能力」的题，风险直接减半。
    """
    types = ["代码生成", "功能迭代", "缺陷修复", "代码重构", "代码理解", "工程化"]
    if capacity <= 0:
        return {name: 0 for name in types}
    new_total = min(capacity, int(capacity * max_new_capability_ratio))
    codegen = (new_total + 1) // 2
    feature = new_total - codegen
    rest = capacity - new_total
    other = {"缺陷修复": 0, "代码重构": 0, "代码理解": 0, "工程化": 0}
    if rest:
        if rest < 4:
            other["缺陷修复"] = rest
        else:
            weights = {"缺陷修复": 5, "代码重构": 2, "代码理解": 2, "工程化": 1}
            total_weight = sum(weights.values())
            assigned = 0
            for name, weight in weights.items():
                share = max(1, int(rest * weight / total_weight))
                other[name] = share
                assigned += share
            order = ["缺陷修复", "代码重构", "代码理解", "工程化"]
            cursor = 0
            while assigned > rest and cursor < 200:
                name = order[cursor % len(order)]
                if other[name] > 1:
                    other[name] -= 1
                    assigned -= 1
                cursor += 1
            cursor = 0
            while assigned < rest and cursor < 200:
                other[order[cursor % len(order)]] += 1
                assigned += 1
                cursor += 1
    return {"代码生成": codegen, "功能迭代": feature, **other}


def compute_capacity(
    derived: dict[str, set[str]] | None,
    *,
    max_per_repo: int = DEFAULT_MAX_PER_REPO,
    max_per_subject: int = DEFAULT_MAX_PER_SUBJECT,
    max_per_mode: int = DEFAULT_MAX_PER_MODE,
    max_new_capability_ratio: float = DEFAULT_MAX_NEW_CAPABILITY_RATIO,
) -> dict[str, object]:
    """这个仓库最多能出多少条题：按「主体 × 需求模式」的座位数算，不按想要多少条算。

    座位数 = min(主体数 × 每主体上限，需求模式数 × 每模式上限，单仓库上限)
    ——三个上限默认分别是每主体 1 条、每模式 1 条、单仓库 35 条。
    为什么按主体数、而且默认每个主体只给 1 条：平台判的是「主体 + 需求模式」，实测判废对里
    同主体对占 71%–76%；剩下那些同主体对里 73%–95% 共享仓库实体词，靠措辞区分不了
    （两种对象词口径的回测见 DEFAULT_MAX_PER_SUBJECT 的注释）。

    所以**先算容量，再决定建几个目录、写几行 Excel**；容量小于想要的数量时，
    要么砍到容量以内，要么换主体更多的仓库，不要靠写得更花来凑数。
    """
    subjects = sorted((derived or {}).keys())
    subject_slots = len(subjects) * max_per_subject
    mode_slots = len(DEMAND_MODE_LEXICON) * max_per_mode
    capacity = min(max_per_repo, subject_slots, mode_slots)
    binding = "repo"
    if subject_slots <= min(max_per_repo, mode_slots):
        binding = "subject"
    elif mode_slots <= min(max_per_repo, subject_slots):
        binding = "mode"
    # 阈值按这个项目的历史仓库标定：常见仓库 8–13 个主体，1 条/主体 就是它们的正常产能，
    # 所以「小于 8」才需要换仓库，不要因为容量只有 10 出头就劝用户换。
    if capacity >= 20:
        verdict = "容量充足"
    elif capacity >= 8:
        verdict = "容量正常，按容量出题"
    else:
        verdict = "容量偏小：这个仓库撑不起一批，建议换主体更多的仓库"
    return {
        "capacity": capacity,
        "binding_limit": binding,
        "subject_count": len(subjects),
        "subjects": subjects,
        "subject_slots": subject_slots,
        "mode_slots": mode_slots,
        "max_per_repo": max_per_repo,
        "max_per_subject": max_per_subject,
        "max_per_mode": max_per_mode,
        "type_mix": suggest_type_mix(capacity, max_new_capability_ratio=max_new_capability_ratio),
        "new_capability_ratio_limit": max_new_capability_ratio,
        "verdict": verdict,
        "rule": "容量 = min(单仓库上限, 主体数 × 每主体上限, 需求模式数 × 每模式上限)；"
                "主体与需求模式在整批里各自唯一",
    }


def evaluate_corpus(corpus: dict | None) -> dict[str, object]:
    """拿真实判词语料回测判据：硬拦召回 + 复核清单召回。

    语料格式见 `tests/fixtures/rule-c-corpus.json`（97 条实际提交过的题面，
    38 对平台点名的同仓库雷同对）。判据改动后必须重跑这道门。
    """
    if not corpus:
        return {}
    batches = corpus.get("batches") or []
    hard_hits = candidate_hits = judged_total = 0
    per_batch: list[dict[str, object]] = []
    for batch in batches:
        records_in = batch.get("records") or []
        if not records_in:
            continue
        repo = batch.get("repo_path")
        derived = derive_repo_modules(Path(repo)) if repo and Path(repo).is_dir() else {}
        labels = [
            f"{row['n']}[{row.get('type') or ''}]" for row in records_in
        ]
        texts = {int(row["n"]): str(row.get("prompt") or "") for row in records_in}
        # 整批维度：这两批当年都是照写不误，新的整批闸门会不会直接把它们拦下。
        batch_result = check(
            list(zip(labels, [str(row.get("prompt") or "") for row in records_in])),
            derived=derived,
            max_unknown=999,
            exempt_labels={label for label in labels
                           if label.split("[")[-1].rstrip("]") in
                           {"工程化", "代码理解", "代码重构", "代码测试"}},
        )
        classified = {}
        for item in batch_result["items"]:
            number = str(item["label"]).split("[")[0]
            if number.isdigit():
                classified[int(number)] = item
        pairs = [tuple(sorted((int(a), int(b)))) for a, b in (batch.get("judged_pairs") or [])]
        hard = candidates = 0
        for a, b in pairs:
            if a not in classified or b not in classified:
                continue
            ra, rb = classified[a], classified[b]
            features = pair_features(ra, rb, texts.get(a, ""), texts.get(b, ""))
            same_subject = bool(ra["module"] and ra["module"] == rb["module"])
            shared_objects = set(ra.get("object_words") or []) & set(rb.get("object_words") or [])
            shared_modes = set(ra.get("modes") or []) & set(rb.get("modes") or [])
            if ((ra["subject_mode"] and ra["subject_mode"] == rb["subject_mode"])
                    or same_subject
                    or shared_modes
                    or (same_subject
                        and len(shared_objects) >= DEFAULT_MIN_SHARED_OBJECT_WORDS)):
                hard += 1
            if features["modules"] or features["modes"]:
                candidates += 1
        judged_total += len(pairs)
        hard_hits += hard
        candidate_hits += candidates
        per_batch.append({
            "project": batch.get("project"),
            "judged_pairs": len(pairs),
            "hard_hits": hard,
            "candidate_hits": candidates,
            "batch_ok": batch_result["ok"],
            "batch_violations": [
                {"kind": item["kind"], "count": item.get("count")}
                for item in batch_result["violations"]
            ],
        })
    if not judged_total:
        return {}
    return {
        "batches": per_batch,
        "judged_pairs": judged_total,
        "hard_recall": round(hard_hits / judged_total, 4),
        "candidate_recall": round(candidate_hits / judged_total, 4),
        "source": corpus.get("source", ""),
    }


def check(
    items: list[tuple[str, str]],
    *,
    max_per_module: int = DEFAULT_MAX_PER_MODULE,
    max_per_repo: int = DEFAULT_MAX_PER_REPO,
    max_per_subject: int = DEFAULT_MAX_PER_SUBJECT,
    max_per_mode: int = DEFAULT_MAX_PER_MODE,
    max_mode_ratio: float = DEFAULT_MAX_MODE_RATIO,
    max_ngram_ratio: float = DEFAULT_MAX_NGRAM_RATIO,
    max_new_capability_ratio: float = DEFAULT_MAX_NEW_CAPABILITY_RATIO,
    review_neighbors: int = DEFAULT_REVIEW_NEIGHBORS,
    min_shared_object_words: int = DEFAULT_MIN_SHARED_OBJECT_WORDS,
    min_candidate_recall: float | None = None,
    judgement_corpus: dict | None = None,
    base_ledger: dict | None = None,
    derived: dict[str, set[str]] | None = None,
    repair_labels: set[str] | None = None,
    exempt_labels: set[str] | None = None,
    require_ledger: bool = False,
    ledger_present: bool = True,
    max_unknown: int = 0,
    max_unknown_modes: int = 0,
) -> dict[str, object]:
    repair_labels = repair_labels or set()
    exempt_labels = exempt_labels or set()
    records = [
        classify(label, text, derived,
                 is_repair=label in repair_labels,
                 exempt=label in exempt_labels)
        for label, text in items
    ]
    # 题面正文只在内存里用于算相似度，不写进台账（台账只留主体、模式与句式指纹）。
    for record, (_, text) in zip(records, items):
        record["_text"] = text
    # 台账里已有的条目也要算进配额，否则跨批次还会超；
    # 但当前这批里已经有的同一条不要再算一遍——验收/复查时工作簿与台账装的是同一批记录，
    # 重复计数会把每个模块的条数翻倍（2026-09-16 实测 cc-9900003：3 条被算成 6 条，
    # 复查时凭空报出 40 处功能点重复与 14 处模块超额）。
    current_labels = {str(record["label"]) for record in records}
    ledger_entries = [
        entry for entry in ((base_ledger or {}).get("entries", []) or [])
        if str(entry.get("label")) not in current_labels
    ]
    # 先数整批的句式指纹，再拿它把模板从句剥掉以后识别需求模式。
    # 不剥的话整批共用一句「…刷新后与返回后…原有的不变」会把所有题的主模式都拽成
    # 「持久化与一致性」——实测 cc-9900003 那 46 条就是这样被拽成同一个模式，模式这一层等于失效。
    ngram_counter: Counter[str] = Counter()
    for record in records:
        ngram_counter.update(record["ngrams"])
    clause_counter: Counter[str] = Counter()
    docs_with_clauses = 0
    for entry in ledger_entries:
        clauses = entry.get("template_clauses") or []
        if clauses:
            docs_with_clauses += 1
        clause_counter.update(clauses)
    total_docs = len(records) + docs_with_clauses
    flagged_clauses: list[dict[str, object]] = []
    if total_docs >= 3:
        for gram, count in ngram_counter.items():
            combined = count + clause_counter.get(gram, 0)
            if combined < 3:
                continue
            ratio = combined / total_docs
            if ratio > max_ngram_ratio:
                flagged_clauses.append({
                    "clause": gram, "count": combined, "ratio": round(ratio, 3),
                })
    flagged_clauses.sort(key=lambda item: (-item["count"], str(item["clause"])))
    flagged_grams = {str(item["clause"]) for item in flagged_clauses}
    for record in records:
        record["template_clauses"] = sorted(flagged_grams & set(record["ngrams"]))
        if record["template_clauses"]:
            cleaned = str(record["_text"])
            for clause in record["template_clauses"]:
                cleaned = cleaned.replace(clause, "")
            mode, modes = detect_modes(cleaned)
            record["mode"], record["modes"] = mode, modes
            record["subject_mode"] = f"{record['module']}|{mode}" if record["module"] and mode else ""
    # 主体配额 +「主体 + 需求模式」唯一：平台判词里「同为围栏模块的属性扩展，功能点不同」
    # 也照样作废，所以默认同一个主体只能 1 条，同一个「主体 + 模式」格子也只能有 1 条。
    primary_counter: Counter[str] = Counter()
    subject_mode_counter: Counter[str] = Counter()
    mode_counter: Counter[str] = Counter()
    for record in records:
        if record["module"]:
            primary_counter[str(record["module"])] += 1
        if record["subject_mode"]:
            subject_mode_counter[str(record["subject_mode"])] += 1
        if record["mode"]:
            mode_counter[str(record["mode"])] += 1
    for entry in ledger_entries:
        if entry.get("module"):
            primary_counter[str(entry["module"])] += 1
        subject_mode = str(entry.get("subject_mode") or "")
        if not subject_mode and entry.get("module") and entry.get("mode"):
            subject_mode = f"{entry['module']}|{entry['mode']}"
        if subject_mode:
            subject_mode_counter[subject_mode] += 1
        if entry.get("mode"):
            mode_counter[str(entry["mode"])] += 1
    violations: list[dict[str, object]] = []
    if require_ledger and not ledger_present:
        violations.append({
            "kind": "台账缺失",
            "why": f"同仓库出题必须先建 {LEDGER_FILENAME} 台账并逐条登记主体与需求模式；"
                   "没有台账就不许写提示词工作簿",
        })
    if not any(record["module"] for record in records) and not primary_counter:
        violations.append({
            "kind": "主体未识别",
            "why": "这批题的主体（改的是仓库里的哪一块）一个都没识别出来，"
                   "先确认提示词不是占位文本，再按主体重新归档",
        })
    unknown = [record["label"] for record in records
               if not record["module"] and not record["repair_round"] and not record["module_exempt"]]
    if len(unknown) > max_unknown:
        violations.append({
            "kind": "主体未识别",
            "count": len(unknown),
            "limit": max_unknown,
            "labels": unknown[:8],
            "why": "题面必须能落回仓库里的某个主体；识别不出来的先补 --repo 指向仓库，"
                   "或确认这条题面到底改的是哪一块，不能带着空白模块去出题",
        })
    # 模式识别不出来同样危险：识别不出就无法证明它不与别的题同模式，等于绕过「同模式重复」。
    unknown_modes = [record["label"] for record in records
                     if not record["mode"] and not record["module_exempt"]]
    if len(unknown_modes) > max_unknown_modes:
        violations.append({
            "kind": "模式未识别",
            "count": len(unknown_modes),
            "limit": max_unknown_modes,
            "labels": unknown_modes[:8],
            "why": "业务能力落不到任何一个需求模式上：识别不出来就没法保证它不与别的题同模式，"
                   "等于绕过「同模式重复」这道硬拦。改题面把它挂到某个具体能力上再出。",
        })
    total = len(records) + len(ledger_entries)
    if total > max_per_repo:
        violations.append({
            "kind": "仓库超出容量",
            "count": total,
            "limit": max_per_repo,
            "why": f"同一个仓库最多出 {max_per_repo} 条：平台是在同一个仓库里两两比对的，"
                   "铺得越多最近邻越近（实测 48/49 条的批次废弃 44%/35%）。"
                   "超出的部分换仓库出，或直接砍掉。",
        })
    # 主体 + 需求模式唯一：这一格重复就是平台判词里的「同为X模块的属性扩展 / 同在X新增能力」。
    for slot, count in subject_mode_counter.items():
        if count > 1:
            violations.append({
                "kind": "主体模式重复",
                "subject_mode": slot,
                "count": count,
                "why": "同一个「主体 + 需求模式」只能出 1 条：平台判词原话「同为围栏模块的属性扩展，"
                       "功能点不同」也判作废。换主体、换需求模式，或把这条并入上一条",
            })
    for subject, count in primary_counter.items():
        if count > max_per_subject:
            violations.append({
                "kind": "同主体超额",
                "subject": subject,
                "count": count,
                "limit": max_per_subject,
                "why": f"同一个主体最多出 {max_per_subject} 条（默认 1）：实测判废对里同主体对占 71%–76%，"
                       "而且同主体对里 73%–95% 共享仓库实体词，靠换措辞或换模式标签都区分不开。"
                       "超出的必须换主体；这个仓库主体不够就换主体更多的仓库。",
            })
    # 同主体 + 同任务家族：放开到每主体 2 条时，两条必须跨家族。
    # 依据：38 对真实判废对里，同家族同主体被判废的有 代码生成↔代码生成 14 对、
    # 代码生成↔功能迭代 11 对、功能迭代↔功能迭代 10 对、缺陷修复↔缺陷修复 3 对；
    # 而「新增能力类 ↔ 缺陷修复」这种跨家族组合一对都没有。所以同家族的第二个主体题必须换主体。
    if max_per_subject >= 2:
        family_of: dict[str, str] = {}
        for record in records:
            match = re.search(r"\[(.+?)\]$", str(record["label"]))
            family_of[str(record["label"])] = TASK_FAMILY.get(match.group(1), "") if match else ""
        same_family: list[dict[str, object]] = []
        for index, record in enumerate(records):
            for other in records[:index]:
                if not record["module"] or record["module"] != other["module"]:
                    continue
                family = family_of.get(str(record["label"]), "")
                if family and family == family_of.get(str(other["label"]), ""):
                    same_family.append({
                        "subject": record["module"],
                        "family": family,
                        "a": other["label"],
                        "b": record["label"],
                    })
        if same_family:
            violations.append({
                "kind": "同主体同家族",
                "count": len(same_family),
                "pairs": same_family[:8],
                "why": "同一个主体上的两条题属于同一个任务家族（都是新增能力，或都是缺陷修复）："
                       "38 对真实判废对里，同家族同主体被判废的有代码生成↔代码生成 14 对、"
                       "代码生成↔功能迭代 11 对、功能迭代↔功能迭代 10 对、缺陷修复↔缺陷修复 3 对，"
                       "而「新增能力类 ↔ 缺陷修复」这种跨家族组合一对都没有。"
                       "第二条要么换成另一个家族，要么换主体出。",
            })
    # 同主体 + 同业务对象：判据的第三根轴。显式放开到每主体 2 条时（--max-per-subject 2），
    # 这两条必须落在**不同的对象面**上，
    # 否则平台按「同为 X 模块的 Y 改造」判废——模式标签不同救不回来（实测 cc-9900003 那批
    # 导航 × 登录态、死链 × 检测中断、导入 × 重复条目、分类 × 重名、账号 × 切换残留 五对全中）。
    if min_shared_object_words > 0:
        collided: list[dict[str, object]] = []
        for index, record in enumerate(records):
            for other in records[:index]:
                if not record["module"] or record["module"] != other["module"]:
                    continue
                shared = set(record["object_words"]) & set(other["object_words"])
                if len(shared) >= min_shared_object_words:
                    collided.append({
                        "subject": record["module"],
                        "a": other["label"],
                        "b": record["label"],
                        "shared_object_words": sorted(shared)[:8],
                    })
        if collided:
            violations.append({
                "kind": "同主体同对象",
                "count": len(collided),
                "limit": min_shared_object_words,
                "pairs": collided[:8],
                "why": f"同一个主体上有 {len(collided)} 对题的**业务对象实词重合 ≥ "
                       f"{min_shared_object_words} 个**：模式标签虽然不同，平台判的是对象，"
                       "照样按「同为该模块的同类改造」判作废。这两条要换主体，"
                       "或者把第二条改到该主体的另一个对象面上。",
            })
    # 同模式重复：判词集合运算显示，判废对「同主体」占 22/38、「跨主体但同模式」占 15/38，
    # 两者都不沾的只有 1 对。所以主体与模式都唯一，就能机械拦下 97.4% 的判废对。
    for mode, count in mode_counter.items():
        if count > max_per_mode:
            violations.append({
                "kind": "同模式重复",
                "mode": mode,
                "count": count,
                "limit": max_per_mode,
                "why": f"「{mode}」在这一批里出了 {count} 条，上限 {max_per_mode} 条："
                       "判词里 15/38 的判废对是跨主体但同模式（「同属待处理清单流程」"
                       "「同一种分析视图」），换主体也躲不开，必须换模式。",
            })
    # 配比类判据只对整批量级生效：三五条的返修小批每一类都占两三成，卡它没意义。
    if len(records) >= 10:
        for mode, count in mode_counter.items():
            ratio = count / total
            if ratio > max_mode_ratio:
                violations.append({
                    "kind": "模式占比超额",
                    "mode": mode,
                    "count": count,
                    "ratio": round(ratio, 3),
                    "limit": max_mode_ratio,
                    "why": f"「{mode}」占了整批的 {ratio:.0%}：判废对里有 29%–59% 是跨主体的"
                           "同需求模式对（同属待处理清单流程 / 同一种分析视图），模式要铺开",
                })
    # 句式指纹：整批复用同一个从句就是判词里的「约束句式一致」，跟措辞好坏无关。
    if flagged_clauses:
        violations.append({
            "kind": "句式指纹重复",
            "clauses": flagged_clauses[:8],
            "limit": max_ngram_ratio,
            "why": f"整批里有 {len(flagged_clauses)} 个 {NGRAM_SIZE} 字片段的使用比例超过 "
                   f"{max_ngram_ratio:.0%}，平台判词里「约束句式一致」本身就是判废理由"
                   "（实测 cc-9900003 有 74% 的题面含同一个从句）。把这些从句拆成多种说法，"
                   "或按环节换句式。",
        })
    # 类型配比：新增能力类（0-1 代码生成 + 功能迭代）历史废弃 43%–57%，
    # 缺陷修复 14%–29%，重构/理解/工程化 0%。整批配比是可控的最大杠杆。
    type_counter: Counter[str] = Counter()
    for record in records:
        match = re.search(r"\[(.+?)\]$", str(record["label"]))
        if match:
            type_counter[match.group(1)] += 1
    new_capability = sum(count for name, count in type_counter.items() if name in NEW_CAPABILITY_TYPES)
    if len(records) >= 10 and new_capability / len(records) > max_new_capability_ratio:
        violations.append({
            "kind": "新增能力类占比超额",
            "count": new_capability,
            "total": len(records),
            "ratio": round(new_capability / len(records), 3),
            "limit": max_new_capability_ratio,
            "why": f"新增能力类（0-1 代码生成 + 功能迭代）占 {new_capability / len(records):.0%}，"
                   f"上限 {max_new_capability_ratio:.0%}。实测这类废弃 43%–57%，而缺陷修复 14%–29%、"
                   "重构/理解/工程化 0%；把配比压下来比改措辞有用。缺陷修复类要同时守难度下限。",
        })

    # 候选清单：同主体 或 需求模式有交集就进清单。平台判词是语义级的，关键词复刻不了它的
    # 配对，但这份清单是超集——拿 38 条真实判废对回测，召回 95%–100%，是唯一托底的一层。
    candidates: list[dict[str, object]] = []
    for index, record in enumerate(records):
        for other in records[:index]:
            shared_modules = set(record["modules"]) & set(other["modules"])
            shared_capabilities = set(record["capabilities"]) & set(other["capabilities"])
            shared_skeletons = set(record["skeletons"]) & set(other["skeletons"])
            shared_words = set(record["capability_words"]) & set(other["capability_words"])
            shared_modes = set(record["modes"]) & set(other["modes"])
            near_words = bool(shared_modules) and len(shared_words) >= FEATURE_NEAR_CANDIDATE
            if not (shared_modules or shared_modes):
                continue
            if shared_modes:
                reason = (
                    f"同主体且需求模式有交集（{'/'.join(sorted(shared_modes))}）："
                    "平台判词把这类直接算同题，功能点不同也照判"
                    if shared_modules else
                    f"跨主体的同需求模式对（{'/'.join(sorted(shared_modes))}）："
                    "判废对里有 29%–59% 正是这种跨模块同模式对"
                )
            else:
                reason = (f"同主体（{'/'.join(sorted(shared_modules))}）"
                          + ("，且能力短语实词重合较多" if near_words else ""))
            candidates.append({
                "a": other["label"],
                "b": record["label"],
                "risk": "高" if shared_modes else "中",
                "modules": sorted(shared_modules),
                "modes": sorted(shared_modes),
                "capabilities": sorted(shared_capabilities),
                "skeletons": sorted(shared_skeletons),
                "shared_words": sorted(shared_words)[:6],
                "why": reason,
            })

    # 最近邻复核清单：每条题列出最像的几条，人工核这一份（覆盖面约八成判废对），
    # 逐条写出「本条与最近邻在主体/需求模式/对象上差在哪」；写不出就换主体或换模式。
    review_list = build_review_list(records, neighbors=review_neighbors)
    flagged_labels = sorted({str(item["a"]) for item in candidates} |
                            {str(item["b"]) for item in candidates})

    calibration = evaluate_corpus(judgement_corpus) if judgement_corpus else {}
    if calibration and min_candidate_recall is not None:
        if calibration["candidate_recall"] < min_candidate_recall:
            violations.append({
                "kind": "判据召回不达标",
                "hard_recall": calibration["hard_recall"],
                "candidate_recall": calibration["candidate_recall"],
                "limit": min_candidate_recall,
                "why": "拿真实判词语料回测，现在这套判据对平台判废对的召回低于下限："
                       "先改判据，不要让这批题带着未知风险写进工作簿",
            })

    # 题面正文与整批 n 字片段只用于内存计算，不进返回值（否则 stdout 与台账都会被撑爆）
    for record in records:
        record.pop("_text", None)
        record.pop("ngrams", None)

    return {
        "ok": not violations,
        "rule": "平台查重规则 C：同主体 + 同需求模式判废（含跨主体同模式）",
        "gate_version": GATE_VERSION,
        "limits": {
            "max_per_repo": max_per_repo,
            "max_per_subject": max_per_subject,
            "max_per_mode": max_per_mode,
            "max_mode_ratio": max_mode_ratio,
            "max_ngram_ratio": max_ngram_ratio,
            "max_new_capability_ratio": max_new_capability_ratio,
            "min_shared_object_words": min_shared_object_words,
        },
        "max_per_module": max_per_module,
        "checked_count": len(records),
        "module_counts": dict(primary_counter.most_common()),
        "subject_mode_counts": dict(subject_mode_counter.most_common()),
        "mode_counts": dict(mode_counter.most_common()),
        "task_type_counts": dict(type_counter.most_common()),
        "new_capability_ratio": (round(new_capability / len(records), 3) if records else 0.0),
        "unknown_labels": unknown,
        "repo_modules": sorted((derived or {}).keys())[:40],
        "violations": violations,
        "candidate_pairs": candidates,
        "candidate_pair_count": len(candidates),
        "review_required_labels": flagged_labels,
        "review_list": review_list,
        "calibration": calibration,
        "items": records,
        "notes": [
            "规则 C 被判废弃的记录不可返修，改措辞无效，只能换题材重出。",
            "平台拿先提交的那条当基准，后提交的语义近题判废弃，所以同仓库必须一次性全局去重；"
            "提交侧再配合分批（每批 ≤10 条），把漏网的损失锁在一批里。",
            "硬拦复刻不了语义判据（实测召回三到五成），真正要守的是第二层："
            "review_required_labels 里的每条题都要写出与最近邻的差异，写不出就换主体或换模式。",
            "0-1 代码生成不豁免规则 C：凭空造的功能只要落在同一主体或同一需求模式上照样判废"
            "（cc-6600009 代码生成废弃率 59%，比功能迭代还高）。",
            "同一个主体默认只出 1 条：判废对里同主体对占 71%–76%，而且同主体对里 73%–95% "
            "共享仓库实体词，换措辞、换模式标签都分不开；主体不够就换更大的仓库。",
        ],
    }


def load_prompt_file(path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if text:
            items.append((f"{path.name}#{index}", text))
    return items


def repo_slug(repo: Path) -> str:
    """仓库台账的稳定文件名：优先用 origin 远端地址，取不到就用绝对路径。"""
    remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=15,
        )
        remote = (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        remote = ""
    base = remote or str(Path(repo).expanduser().resolve())
    base = base.replace("https://github.com/", "").replace("git@github.com:", "")
    base = re.sub(r"\.git$", "", base)
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", base).strip("-").lower()
    return slug or "unknown-repo"


def repo_ledger_path(repo: Path) -> Path:
    """同仓库跨批次台账：不跟着父目录走，换一个父目录也继续算配额。"""
    return REPO_LEDGER_ROOT / f"{repo_slug(repo)}.json"


def load_ledger(path: Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def merge_ledgers(*ledgers: dict | None) -> dict:
    """把父目录台账与仓库台账合并：按 label 去重，配额与功能点都累计。"""
    merged: dict[str, dict] = {}
    for ledger in ledgers:
        for entry in (ledger or {}).get("entries", []) or []:
            merged[str(entry.get("label"))] = entry
    return {"entries": list(merged.values())}


def write_repo_ledger(repo: Path, entries: list[dict], *, gate_result: dict | None = None) -> Path:
    path = repo_ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = merge_ledgers(load_ledger(path))
    merged = {str(item.get("label")): item for item in existing.get("entries", [])}
    for entry in entries:
        merged[str(entry.get("label"))] = ledger_entry(entry)
    payload = {
        "gate_version": GATE_VERSION,
        "repo": str(Path(repo).expanduser().resolve()),
        "entries": list(merged.values()),
    }
    if gate_result is not None:
        payload["module_counts"] = gate_result.get("module_counts", {})
        payload["subject_mode_counts"] = gate_result.get("subject_mode_counts", {})
        payload["mode_counts"] = gate_result.get("mode_counts", {})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


LEDGER_ENTRY_FIELDS = (
    "label", "module", "modules", "mode", "modes", "subject_mode", "capabilities",
    "skeletons", "feature_point", "capability_words", "object_words", "template_clauses",
    "repair_round", "module_exempt",
)


def ledger_entry(record: dict) -> dict:
    """台账条目只留结构化字段：题面正文与整批 n 字片段不进台账，避免文件膨胀。"""
    return {key: record[key] for key in LEDGER_ENTRY_FIELDS if key in record}


def build_feature_points(repo: Path, derived: dict[str, set[str]], result: dict) -> dict:
    """出题前先落一份题位表：主体（含关键词）+ 需求模式 + 已被占用的「主体 × 模式」格子。

    出题时按这张表分配题位，而不是写完几十条再查重：一个格子只能坐一条题，
    写不出差异就得换主体或换模式。这就是把「事后查重」换成「事前排座」。
    """
    occupied: dict[str, list[str]] = {}
    subjects: dict[str, list[str]] = {}
    modes: dict[str, list[str]] = {}
    for entry in result.get("items", []) or []:
        slot = entry.get("subject_mode") or ""
        if slot:
            occupied.setdefault(str(slot), []).append(str(entry.get("label")))
        if entry.get("module"):
            subjects.setdefault(str(entry["module"]), []).append(str(entry.get("label")))
        if entry.get("mode"):
            modes.setdefault(str(entry["mode"]), []).append(str(entry.get("label")))
    return {
        "gate_version": GATE_VERSION,
        "repo": str(Path(repo).expanduser().resolve()),
        "rule": "每条题面必须落回一个主体；同一「主体 + 需求模式」只出 1 条，"
                f"同一主体最多 {DEFAULT_MAX_PER_SUBJECT} 条，单仓库最多 {DEFAULT_MAX_PER_REPO} 条，"
                f"单模式占比不超过 {DEFAULT_MAX_MODE_RATIO:.0%}，"
                f"新增能力类占比不超过 {DEFAULT_MAX_NEW_CAPABILITY_RATIO:.0%}",
        "capacity": compute_capacity(derived),
        "modules": [
            {"module": name, "keywords": sorted(keywords)[:40]}
            for name, keywords in sorted(derived.items())
        ],
        "demand_modes": sorted(DEMAND_MODE_LEXICON),
        "occupied_subject_modes": occupied,
        "occupied_subjects": subjects,
        "occupied_modes": modes,
        "module_counts": result.get("module_counts", {}),
        "mode_counts": result.get("mode_counts", {}),
        "subject_mode_counts": result.get("subject_mode_counts", {}),
        "task_type_counts": result.get("task_type_counts", {}),
        "new_capability_ratio": result.get("new_capability_ratio", 0.0),
        "limits": result.get("limits", {}),
        "unknown_labels": result.get("unknown_labels", []),
    }


def run_gate(
    parent: Path,
    *,
    workbook: str | None = None,
    repo: Path | None = None,
    require_ledger: bool = True,
    max_per_module: int = DEFAULT_MAX_PER_MODULE,
    max_per_repo: int = DEFAULT_MAX_PER_REPO,
    max_per_subject: int = DEFAULT_MAX_PER_SUBJECT,
    max_per_mode: int = DEFAULT_MAX_PER_MODE,
    max_mode_ratio: float = DEFAULT_MAX_MODE_RATIO,
    max_ngram_ratio: float = DEFAULT_MAX_NGRAM_RATIO,
    max_new_capability_ratio: float = DEFAULT_MAX_NEW_CAPABILITY_RATIO,
    judgement_corpus: dict | None = None,
    min_candidate_recall: float | None = None,
    max_unknown: int = 0,
) -> dict:
    """写入路径专用的整批闸门：读工作簿 → 派生模块 → 合并父目录与仓库台账 → 出结论。"""
    workbook_name = workbook or workbook_lib.DEFAULT_WORKBOOK
    items, repair_labels, exempt_labels = load_workbook_prompts(parent, workbook_name)
    derived = derive_repo_modules(repo) if repo else {}
    merged = merge_ledgers(load_ledger(parent / LEDGER_FILENAME),
                           load_ledger(repo_ledger_path(repo)) if repo else {})
    result = check(
        items,
        max_per_module=max_per_module,
        max_per_repo=max_per_repo,
        max_per_subject=max_per_subject,
        max_per_mode=max_per_mode,
        max_mode_ratio=max_mode_ratio,
        max_ngram_ratio=max_ngram_ratio,
        max_new_capability_ratio=max_new_capability_ratio,
        judgement_corpus=judgement_corpus,
        min_candidate_recall=min_candidate_recall,
        base_ledger=merged,
        derived=derived,
        repair_labels=repair_labels,
        exempt_labels=exempt_labels,
        require_ledger=require_ledger,
        ledger_present=(parent / LEDGER_FILENAME).exists(),
        max_unknown=max_unknown,
    )
    result["gate_version"] = GATE_VERSION
    result["workbook"] = str((parent / workbook_name).resolve())
    result["repo"] = str(repo) if repo else ""
    return result


def load_workbook_prompts(parent: Path, workbook_name: str) -> list[tuple[str, str]]:
    records = workbook_lib.read_workbook(parent / workbook_name)
    items: list[tuple[str, str]] = []
    repair_labels: set[str] = set()
    exempt_labels: set[str] = set()
    for record in records:
        prompt = str(record.get("提示词", "") or "").strip()
        if not prompt:
            continue
        label = str(record.get("子文件夹名称", "") or "未命名")
        task_type = str(record.get("任务类型", "") or "").strip()
        if task_type:
            label = f"{label}[{task_type}]"
        if task_type in {"Bug 修复", "Bug修复"} or str(record.get("提示词类型", "") or "").startswith("修复"):
            repair_labels.add(label)
        # 工程化 / 代码理解 / 代码重构 这三类题本来就不一定落在某个界面组件上，
        # 不强制要求能识别出模块，其余类型必须落回仓库里的模块。
        if task_type in {"工程化", "代码理解", "代码重构"}:
            exempt_labels.add(label)
        items.append((label, prompt))
    return items, repair_labels, exempt_labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", help="父目录，默认读取其中的 solo-create-prompts.xlsx")
    parser.add_argument("--workbook", default=workbook_lib.DEFAULT_WORKBOOK)
    parser.add_argument("--prompts-file", help="每行一条提示词的文本文件，与 --parent 二选一")
    parser.add_argument("--repo", help="仓库路径；给了就从仓库源码派生模块词典，题面必须能落回这些模块")
    parser.add_argument("--ledger", help="已有台账 json；默认用父目录下的 repo-theme-ledger.json")
    parser.add_argument("--write-ledger", action="store_true", help="把本次分类结果写进台账")
    parser.add_argument("--require-ledger", action="store_true",
                        help="批量出题的硬前置：台账不存在就直接判不通过")
    parser.add_argument("--write-feature-points", nargs="?", const="", default=None,
                        help="写一份功能点清单（默认写到父目录的 repo-feature-points.json）；"
                             "出题前先生成，按清单分配题位")
    parser.add_argument("--capacity", action="store_true",
                        help="只算这个仓库能出多少条题（主体数 × 每主体上限，再和单仓库上限取小），"
                             "给出建议类型配比；建仓前先跑这一步决定目录数与 Excel 行数")
    parser.add_argument("--capacity-file", nargs="?", const="", default=None,
                        help="把容量结果写成 JSON（默认写到父目录的 repo-capacity.json）")
    parser.add_argument("--version", action="store_true", help="打印闸门版本")
    parser.add_argument("--max-per-module", type=int, default=DEFAULT_MAX_PER_MODULE,
                        help="已废弃：2026-09-16 起改由 --max-per-subject（默认 1）与 "
                             "--max-per-mode（默认 1）约束，这个参数只保留兼容")
    parser.add_argument("--max-per-repo", type=int, default=DEFAULT_MAX_PER_REPO,
                        help=f"单仓库最多出几条（跨批次累计），默认 {DEFAULT_MAX_PER_REPO}")
    parser.add_argument("--max-per-subject", type=int, default=DEFAULT_MAX_PER_SUBJECT,
                        help=f"同一主体最多出几条，默认 {DEFAULT_MAX_PER_SUBJECT}")
    parser.add_argument("--max-per-mode", type=int, default=DEFAULT_MAX_PER_MODE,
                        help=f"同一需求模式最多出几条，默认 {DEFAULT_MAX_PER_MODE}；"
                             "判废对里 15/38 是跨主体同模式，换主体躲不开，只能换模式")
    parser.add_argument("--max-mode-ratio", type=float, default=DEFAULT_MAX_MODE_RATIO,
                        help=f"单个需求模式占整批的比例上限，默认 {DEFAULT_MAX_MODE_RATIO}")
    parser.add_argument("--max-ngram-ratio", type=float, default=DEFAULT_MAX_NGRAM_RATIO,
                        help=f"单个 {NGRAM_SIZE} 字句式片段的使用比例上限，默认 "
                             f"{DEFAULT_MAX_NGRAM_RATIO}")
    parser.add_argument("--max-new-capability-ratio", type=float,
                        default=DEFAULT_MAX_NEW_CAPABILITY_RATIO,
                        help="新增能力类（0-1 代码生成 + 功能迭代）占比上限，默认 "
                             f"{DEFAULT_MAX_NEW_CAPABILITY_RATIO}")
    parser.add_argument("--review-neighbors", type=int, default=DEFAULT_REVIEW_NEIGHBORS,
                        help=f"最近邻复核清单每条列几个邻居，默认 {DEFAULT_REVIEW_NEIGHBORS}")
    parser.add_argument("--min-shared-object-words", type=int,
                        default=DEFAULT_MIN_SHARED_OBJECT_WORDS,
                        help="同主体两条题的业务对象实词重合到几个就判「同主体同对象」，"
                             f"默认 {DEFAULT_MIN_SHARED_OBJECT_WORDS}；传 0 关闭这条判据")
    parser.add_argument("--judgement-corpus",
                        help="真实判词语料（默认找 skill 里的 tests/fixtures/rule-c-corpus.json）；"
                             "给了就回测判据召回")
    parser.add_argument("--min-candidate-recall", type=float, default=DEFAULT_MIN_CANDIDATE_RECALL,
                        help="复核清单对语料判废对的召回下限，默认 "
                             f"{DEFAULT_MIN_CANDIDATE_RECALL}")
    parser.add_argument("--no-calibration", action="store_true",
                        help="跳过判词回归（只在调试判据时用，正式出题不许跳）")
    parser.add_argument("--max-unknown", type=int, default=0,
                        help="允许几条题面识别不到主体，默认 0（一条都不许）")
    parser.add_argument("--max-unknown-modes", type=int, default=0,
                        help="允许几条题面识别不到需求模式，默认 0（一条都不许）")
    args = parser.parse_args()
    if args.version:
        print(json.dumps({
            "gate_version": GATE_VERSION,
            "rule": "同仓库主题去重（主体 + 需求模式）",
            "limits": {
                "max_per_repo": DEFAULT_MAX_PER_REPO,
                "max_per_subject": DEFAULT_MAX_PER_SUBJECT,
                "max_per_mode": DEFAULT_MAX_PER_MODE,
                "max_mode_ratio": DEFAULT_MAX_MODE_RATIO,
                "max_ngram_ratio": DEFAULT_MAX_NGRAM_RATIO,
                "max_new_capability_ratio": DEFAULT_MAX_NEW_CAPABILITY_RATIO,
            },
            "min_candidate_recall": DEFAULT_MIN_CANDIDATE_RECALL,
        }, ensure_ascii=False))
        return

    parent: Path | None = None
    repair_labels: set[str] = set()
    exempt_labels: set[str] = set()
    if args.prompts_file:
        items = load_prompt_file(Path(args.prompts_file).expanduser().resolve())
    elif args.parent:
        parent = Path(args.parent).expanduser().resolve()
        if not parent.is_dir():
            raise SystemExit(f"Parent directory does not exist: {parent}")
        items, repair_labels, exempt_labels = load_workbook_prompts(parent, args.workbook)
    elif args.repo and (args.capacity or args.capacity_file is not None):
        # 只算容量：建仓前还没有父目录下的工作簿，最多只有一个源码子目录。
        items = []
    else:
        raise SystemExit("必须提供 --parent 或 --prompts-file 之一")

    ledger_path = Path(args.ledger).expanduser().resolve() if args.ledger else (
        parent / LEDGER_FILENAME if parent else None
    )
    base_ledger = None
    if ledger_path and ledger_path.exists():
        base_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    repo_path = Path(args.repo).expanduser().resolve() if args.repo else None
    # 台账按仓库累计：父目录台账 + 同仓库跨批次台账，两边都算进配额
    if repo_path:
        base_ledger = merge_ledgers(base_ledger, load_ledger(repo_ledger_path(repo_path)))
    derived = derive_repo_modules(repo_path) if repo_path else {}
    # 建仓前第一步：先算这个仓库能出几条题，再决定建几个目录、写几行 Excel。
    if args.capacity or args.capacity_file is not None:
        capacity = compute_capacity(
            derived, max_per_repo=args.max_per_repo, max_per_subject=args.max_per_subject,
            max_new_capability_ratio=args.max_new_capability_ratio,
        )
        capacity["gate_version"] = GATE_VERSION
        if args.capacity_file is not None:
            target = (
                Path(args.capacity_file).expanduser().resolve()
                if args.capacity_file
                else (parent / CAPACITY_FILENAME if parent else Path(CAPACITY_FILENAME))
            )
            target.write_text(
                json.dumps(capacity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            capacity["capacity_file"] = str(target)
        print(json.dumps(capacity, ensure_ascii=False, indent=2))
        return
    # 判词回归：判据不能只靠「我觉得更严了」，必须拿真实判废对量一遍。
    corpus_path: Path | None = None
    if not args.no_calibration:
        if args.judgement_corpus:
            corpus_path = Path(args.judgement_corpus).expanduser().resolve()
        elif DEFAULT_CORPUS_PATH.exists():
            corpus_path = DEFAULT_CORPUS_PATH
    judgement_corpus = None
    if corpus_path and corpus_path.exists():
        try:
            judgement_corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            judgement_corpus = None
    result = check(
        items,
        max_per_module=args.max_per_module,
        max_per_repo=args.max_per_repo,
        max_per_subject=args.max_per_subject,
        max_per_mode=args.max_per_mode,
        max_mode_ratio=args.max_mode_ratio,
        max_ngram_ratio=args.max_ngram_ratio,
        max_new_capability_ratio=args.max_new_capability_ratio,
        review_neighbors=args.review_neighbors,
        min_shared_object_words=args.min_shared_object_words,
        judgement_corpus=judgement_corpus,
        min_candidate_recall=(None if args.no_calibration else args.min_candidate_recall),
        base_ledger=base_ledger,
        derived=derived,
        repair_labels=repair_labels,
        exempt_labels=exempt_labels,
        require_ledger=args.require_ledger,
        ledger_present=bool(ledger_path and ledger_path.exists()),
        max_unknown=args.max_unknown,
        max_unknown_modes=args.max_unknown_modes,
    )
    result["gate_version"] = GATE_VERSION
    if corpus_path:
        result["judgement_corpus"] = str(corpus_path)

    if args.write_ledger and ledger_path:
        merged: dict[str, dict] = {}
        for entry in (base_ledger or {}).get("entries", []) or []:
            merged[str(entry.get("label"))] = entry
        for entry in result["items"]:
            merged[str(entry["label"])] = ledger_entry(entry)
        ledger_path.write_text(
            json.dumps({
                "gate_version": GATE_VERSION,
                "rule": result["rule"],
                "limits": result.get("limits", {}),
                "module_counts": result["module_counts"],
                "subject_mode_counts": result.get("subject_mode_counts", {}),
                "mode_counts": result.get("mode_counts", {}),
                "entries": list(merged.values()),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result["ledger"] = str(ledger_path)
        if repo_path:
            result["repo_ledger"] = str(
                write_repo_ledger(repo_path, list(merged.values()), gate_result=result)
            )

    if args.write_feature_points is not None:
        target = (
            Path(args.write_feature_points).expanduser().resolve()
            if args.write_feature_points
            else (parent / FEATURE_POINTS_FILENAME if parent else Path(FEATURE_POINTS_FILENAME))
        )
        payload = build_feature_points(repo_path or Path.cwd(), derived, result)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["feature_points_file"] = str(target)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
