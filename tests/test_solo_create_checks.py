"""出题侧两个闸门的回归用例（2026-09-16 按平台规则 C 与 P3 判词补）。

背景：cc-6600009 那批 48 条里 21 条按规则 C（同仓库语义雷同）判废弃、1 条按 P3（题目过于简单）
判废弃，两条判词都写「不可返修，只改写措辞无效」。这里把当时的口径固化成用例。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class RepoThemeTest(unittest.TestCase):
    def test_repo_modules_derive_from_source(self):
        """主体词典必须从仓库自身派生：一条题面能落回某个组件才算合格。"""
        import tempfile

        from check_repo_theme import check, derive_repo_modules

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            components = repo / "frontend" / "src" / "components"
            components.mkdir(parents=True)
            (components / "RecordingPanel.tsx").write_text(
                "const label = '历史录制'; const tip = '开始录制'; const hint = '删除录制记录';",
                encoding="utf-8",
            )
            (components / "BrainStateDashboard.tsx").write_text(
                "const label = '脑状态'; const tip = '专注度'; const hint = '疲劳度';",
                encoding="utf-8",
            )
            derived = derive_repo_modules(repo)
            self.assertIn("RecordingPanel", derived)
            self.assertIn("录制", derived["RecordingPanel"])
            result = check(
                [("a", "新增录制列表分页：录制很多时按页浏览，翻页后保持当前通道的录制顺序。")],
                derived=derived,
            )
            self.assertEqual(result["items"][0]["module"], "RecordingPanel")

    def test_same_subject_same_mode_is_hard_blocked(self):
        """同一「主体 + 需求模式」只能有一条。

        平台判词原话：「同为围栏模块的属性扩展，功能点不同」——两条题功能点完全不同也照样判废，
        所以这一格重复必须硬拦。
        """
        from check_repo_theme import check

        derived = {"DeviceRegistration": {"设备", "注册", "批量"}}
        items = [
            ("a", "新增设备批量注册：多台设备一次提交，逐台给出成功失败清单。"),
            ("b", "新增设备批量下发参数：多台设备一次提交配置，逐台给出成功失败清单。"),
        ]
        result = check(items, derived=derived)
        self.assertFalse(result["ok"], result["violations"])
        self.assertTrue(
            any(v["kind"] == "主体模式重复" for v in result["violations"]), result["violations"]
        )

    def test_cross_subject_same_mode_is_candidate(self):
        """跨主体的同需求模式对不会硬拦，但必须进复核清单。"""
        from check_repo_theme import check

        derived = {
            "AlarmCenter": {"告警", "升级", "阈值"},
            "DeviceOffline": {"设备", "离线", "阈值"},
        }
        items = [
            ("a", "新增告警批量确认：多选若干条未确认的告警一次提交，逐条给出结果。"),
            ("b", "新增设备批量注册：多选若干台设备一次提交，逐台给出结果。"),
        ]
        result = check(items, derived=derived)
        pairs = result["candidate_pairs"]
        self.assertTrue(pairs, "跨主体同模式必须进候选清单")
        self.assertTrue(any(pair["modes"] for pair in pairs), pairs)
        self.assertTrue(any(pair["risk"] == "高" for pair in pairs), pairs)

    def test_repo_capacity_blocks_oversized_batch(self):
        """单仓库条数上限：平台是在同一个仓库里两两比，铺得越多越近。"""
        from check_repo_theme import check

        derived = {"RecordingPanel": {"录制", "回看", "通道"}}
        items = [
            (f"{index}[代码生成]", f"新增录制回看能力第 {index} 种：按通道整理历史录制的第 {index} 个侧面。")
            for index in range(1, 52)
        ]
        # 单仓库上限 2026-09-23 起是 50，所以要用 51 条才能单独触发「仓库超出容量」
        result = check(items, derived=derived, max_unknown=99)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(v["kind"] == "仓库超出容量" for v in result["violations"]), result["violations"]
        )

    def test_subject_over_quota_is_blocked(self):
        """同一个主体最多 2 条：判词里同主体的第三条题同样进雷同池。"""
        from check_repo_theme import check

        derived = {"AlarmCenter": {"告警", "升级", "阈值", "预案", "交接"}}
        items = [
            ("a", "新增告警处置预案：按告警类型维护处置步骤，逐条勾选后自动结单。"),
            ("b", "新增告警超时升级：超过设定时长未确认的告警自动提升级别。"),
            ("c", "新增告警跨班交接：把未确认的告警连同处理进度转交给下一班。"),
        ]
        result = check(items, derived=derived)
        self.assertTrue(
            any(v["kind"] == "同主体超额" for v in result["violations"]), result["violations"]
        )

    def test_repeated_template_clause_is_blocked(self):
        """整批复用同一个从句就是判词里的「约束句式一致」，与措辞好坏无关。

        实测 cc-9900003 那批 46 条里有 74% 含同一个「刷新后与返回后」从句，
        而两个被判废的批次最高只有 10%——这条是优化过程中新引入的风险。
        """
        from check_repo_theme import check

        derived = {"RecordingPanel": {"录制", "回看"}}
        items = [
            (f"{index}[缺陷修复]", f"录制回看第 {index} 处状态对不上，期望修好以后刷新后与返回后保持一致。")
            for index in range(1, 11)
        ]
        result = check(items, derived=derived, max_unknown=99, max_per_repo=99)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(v["kind"] == "句式指纹重复" for v in result["violations"]), result["violations"]
        )

    def test_new_capability_ratio_is_blocked(self):
        """新增能力类占比过高要拦：实测这类废弃 43%–57%，缺陷修复只有 14%–29%。"""
        from check_repo_theme import check

        derived = {"RecordingPanel": {"录制", "回看", "通道", "波形", "标记"}}
        items = [
            (f"{index}[代码生成]", f"新增录制回看第 {index} 个展示能力：按通道排名并标出第 {index} 项。")
            for index in range(1, 13)
        ]
        result = check(items, derived=derived, max_unknown=99, max_per_repo=99)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(v["kind"] == "新增能力类占比超额" for v in result["violations"]),
            result["violations"],
        )

    def test_unknown_module_is_a_violation(self):
        """题面落不回仓库主体时要拦：不认主体就查不出同主体同模式的重复。"""
        from check_repo_theme import check

        result = check(
            [("a", "新增一件小事：把标题改一下。")],
            derived={"RecordingPanel": {"录制", "回放"}},
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any(v["kind"] == "主体未识别" for v in result["violations"]), result["violations"])

    def test_missing_ledger_blocks_batch(self):
        """批量出题的硬前置：没有台账不许写工作簿。"""
        from check_repo_theme import check

        result = check(
            [("a", "新增告警处置预案：按告警类型维护可复用的处置步骤。")],
            require_ledger=True,
            ledger_present=False,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any(v["kind"] == "台账缺失" for v in result["violations"]), result["violations"])

    def test_diverse_batch_passes(self):
        """主体、模式、类型都铺开的批次要能通过。"""
        from check_repo_theme import check

        items = [
            ("t1[代码生成]", "新增告警处置预案：按告警类型维护可复用的处置步骤，逐条勾选后自动结单。"),
            ("t2[缺陷修复]", "围栏工作台的准入名单点了不生效：越界的设备没有生成闯入记录。"),
            ("t3[代码重构]", "设备导出的取值写法散在多处，希望收拢到同一份写法里，行为不变。"),
            ("t4[代码理解]", "想理清一条轨迹从采集到回放的完整链路，每一步在哪里取值。"),
        ]
        result = check(items, max_unknown=99)
        self.assertTrue(result["ok"], result["violations"])
        self.assertEqual(result["new_capability_ratio"], 0.25)

    def test_corpus_recall_and_historical_batches_are_rejected(self):
        """判词回归：拿 38 对真实判废对量复核清单的召回，并要求历史两批被判不通过。

        这两批是实际提交过的（cc-6600009 48 条废 21 条、cc-6600011 49 条废 17 条）。
        判据改动必须过这道门：复核清单召回 < 0.90 就不许写表。
        """
        import json
        from pathlib import Path

        from check_repo_theme import evaluate_corpus

        corpus_path = Path(__file__).resolve().parent / "fixtures" / "rule-c-corpus.json"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        calibration = evaluate_corpus(corpus)
        self.assertEqual(calibration["judged_pairs"], len(
            [pair for batch in corpus["batches"] for pair in batch["judged_pairs"]]
        ))
        self.assertGreaterEqual(calibration["candidate_recall"], 0.90, calibration)
        by_project = {item["project"]: item for item in calibration["batches"]}
        self.assertFalse(by_project["cc-6600009"]["batch_ok"], by_project["cc-6600009"])
        self.assertFalse(by_project["cc-6600011"]["batch_ok"], by_project["cc-6600011"])
        for project in ("cc-6600009", "cc-6600011"):
            kinds = {item["kind"] for item in by_project[project]["batch_violations"]}
            self.assertIn("仓库超出容量", kinds, by_project[project])

    def test_ledger_repeating_current_batch_is_not_counted_twice(self):
        """复查同一批时，台账里装的就是这批自己的记录，不能再算一遍。

        2026-09-16 实测 cc-9900003：写完台账后原地复查，每个主体的 3 条被算成 6 条，
        凭空报出 40 处「功能点重复」与 14 处「模块超额」，把整批题判成不通过。
        """
        from check_repo_theme import check

        items = [
            ("t1", "新增告警批量确认：多选若干条未确认的告警一次提交，逐条给出结果。"),
            ("t2", "新增围栏导出：把选定的围栏与准入名单打包导出成文件。"),
        ]
        # 台账内容与当前这批完全相同（写入台账后立即复查就是这个状态）
        ledger = {
            "entries": [
                {"label": "t1", "module": "告警中心", "mode": "批量操作",
                 "subject_mode": "告警中心|批量操作"},
                {"label": "t2", "module": "围栏工作台", "mode": "导入导出",
                 "subject_mode": "围栏工作台|导入导出"},
            ]
        }
        result = check(items, base_ledger=ledger)
        self.assertEqual(result["module_counts"].get("告警中心"), 1, result["module_counts"])
        self.assertEqual(result["module_counts"].get("围栏工作台"), 1, result["module_counts"])
        self.assertEqual(result["violations"], [])
        self.assertTrue(result["ok"])

        # 台账里是别的批次的记录时，仍然要算进配额
        other_ledger = {
            "entries": [
                {"label": "old-1", "module": "告警中心", "mode": "新增展示视图",
                 "subject_mode": "告警中心|新增展示视图"},
                {"label": "old-2", "module": "告警中心", "mode": "新增展示视图",
                 "subject_mode": "告警中心|新增展示视图"},
            ]
        }
        result = check(items, base_ledger=other_ledger)
        self.assertEqual(result["module_counts"].get("告警中心"), 3, result["module_counts"])
        self.assertTrue(
            any(v["kind"] == "同主体超额" for v in result["violations"]), result["violations"]
        )


class CapacityTest(unittest.TestCase):
    """建仓数量必须由容量决定，不能固定 46 条。

    2026-09-16 实测：把 48/49 条压在同一个仓库里，规则 C 废弃 44%/35%。
    容量 = min(单仓库上限 50, 主体数 × 每主体上限 2, 需求模式数 13 × 每模式上限 3)，
    建目录数与 Excel 行数都按它来（2026-09-23 起单仓库上限由 35 放宽到 50）。
    每主体放第二条的依据是任务家族：同家族同主体被判废的有 38 对里的绝大部分，
    而「新增能力类 ↔ 缺陷修复」跨家族组合 0/38，所以第二条必须跨家族。
    """

    def test_capacity_is_subject_limited(self):
        from check_repo_theme import compute_capacity

        derived = {f"Comp{index}": {"词"} for index in range(11)}
        capacity = compute_capacity(derived)
        # 默认每主体 2 条 → 11 × 2 = 22（模式上限 13 × 2 = 26 不构成瓶颈）
        self.assertEqual(capacity["capacity"], 22)
        self.assertEqual(capacity["binding_limit"], "subject")
        self.assertEqual(capacity["subject_count"], 11)

    def test_capacity_defaults_to_three_per_mode(self):
        from check_repo_theme import DEMAND_MODE_LEXICON, compute_capacity

        derived = {f"Comp{index}": {"词"} for index in range(40)}
        capacity = compute_capacity(derived)
        # 2026-09-22 起默认每模式 3 条：模式席位 = 13 × 3 = 39；
        # 单仓库上限 2026-09-23 放宽到 50 之后，主体够多时先卡在模式轴（39 < 50）
        self.assertEqual(capacity["mode_slots"], len(DEMAND_MODE_LEXICON) * 3)
        self.assertEqual(capacity["capacity"], len(DEMAND_MODE_LEXICON) * 3)
        self.assertEqual(capacity["binding_limit"], "mode")

    def test_capacity_is_mode_limited_when_pinned_back_to_two(self):
        from check_repo_theme import DEMAND_MODE_LEXICON, compute_capacity

        derived = {f"Comp{index}": {"词"} for index in range(40)}
        capacity = compute_capacity(derived, max_per_mode=2)
        # 显式收回旧口径：再大的仓库也只能出「模式数 × 2」条
        self.assertEqual(capacity["capacity"], len(DEMAND_MODE_LEXICON) * 2)
        self.assertEqual(capacity["binding_limit"], "mode")

    def test_capacity_is_repo_limited_when_modes_are_wide_enough(self):
        from check_repo_theme import DEMAND_MODE_LEXICON, compute_capacity

        derived = {f"Comp{index}": {"词"} for index in range(40)}
        capacity = compute_capacity(derived, max_per_mode=4)
        # 模式席位 13 × 4 = 52 >= 50，主体也够多 → 这时才真正卡在单仓库上限 50
        self.assertGreaterEqual(len(DEMAND_MODE_LEXICON) * 4, 50)
        self.assertEqual(capacity["capacity"], 50)
        self.assertEqual(capacity["binding_limit"], "repo")

    def test_type_mix_respects_new_capability_ratio(self):
        from check_repo_theme import compute_capacity

        for subjects in (6, 11, 20, 40):
            derived = {f"Comp{index}": {"词"} for index in range(subjects)}
            capacity = compute_capacity(derived)
            mix = capacity["type_mix"]
            total = sum(mix.values())
            self.assertEqual(total, capacity["capacity"], (subjects, mix))
            new_capability = mix["代码生成"] + mix["功能迭代"]
            self.assertLessEqual(new_capability, capacity["capacity"] / 2 + 0.5, mix)
            self.assertGreaterEqual(mix["缺陷修复"], 1, mix)

    def test_tiny_repo_is_flagged(self):
        from check_repo_theme import compute_capacity

        capacity = compute_capacity({"OnlyComponent": {"词"}})
        # 默认每主体 2 条 → 单个主体的仓库是 2 条，仍然远低于一批的量
        self.assertEqual(capacity["capacity"], 2)
        self.assertIn("撑不起一批", capacity["verdict"])

    def test_create_batch_defaults_to_capacity(self):
        """建仓脚本不传数量时按容量建，且计划数不超过容量。"""
        import tempfile

        import create_batch_local_tasks as seed

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            source = parent / "solo-9900003"
            components = source / "frontend" / "src" / "components"
            components.mkdir(parents=True)
            for index in range(4):
                (components / f"Panel{index}.tsx").write_text(
                    f"const label = '面板{index}标题'; const tip = '打开面板{index}';"
                    f"const hint = '删除面板{index}标签';",
                    encoding="utf-8",
                )
            overrides = dict.fromkeys(seed.TYPE_BY_SLUG)
            counts, capacity = seed.capacity_driven_counts(
                source, overrides, max_per_repo=35, max_per_subject=2,
            )
            self.assertEqual(capacity["capacity"], 8)
            self.assertEqual(sum(counts.values()), 8)
            specs = [seed.TaskSpec(slug, seed.TYPE_BY_SLUG[slug], counts[slug])
                     for slug in seed.TYPE_BY_SLUG]
            tasks = seed.planned_tasks("9900003", specs, "concat")
            self.assertEqual(len(tasks), 8)
            self.assertEqual([task["index"] for task in tasks], list(range(1, 9)))

    def test_capacity_cli_reports_suggestion(self):
        """--capacity 必须给出容量、主体清单与建议配比（建仓前先看这一步）。"""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            components = repo / "frontend" / "src" / "components"
            components.mkdir(parents=True)
            for index in range(6):
                (components / f"View{index}.tsx").write_text(
                    f"const a = '视图{index}列表'; const b = '新增视图{index}';"
                    f"const c = '删除视图{index}';",
                    encoding="utf-8",
                )
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "check_repo_theme.py"),
                 "--repo", str(repo), "--capacity"],
                capture_output=True, text=True, check=True,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["capacity"], 12)
            self.assertEqual(len(payload["subjects"]), 6)
            self.assertEqual(sum(payload["type_mix"].values()), 12)

    def test_same_family_second_prompt_is_blocked(self):
        """同一个主体的第二条题必须跨任务家族：同家族的两条是判废对的来源。"""
        from check_repo_theme import check

        derived = {"Navbar": {"导航", "登录", "退出", "失效"}}
        items = [
            ("a[功能迭代]", "顶部导航想按会话状态做出区分：临近失效时切回未登录的样子。"),
            ("b[功能迭代]", "顶部导航想按身份区分可见范围：未登录时点入口先要求登录。"),
        ]
        result = check(items, derived=derived, max_unknown=99)
        self.assertFalse(result["ok"], result["violations"])
        self.assertTrue(
            any(v["kind"] == "同主体同家族" for v in result["violations"]), result["violations"]
        )

    def test_cross_family_second_prompt_on_same_subject_is_allowed(self):
        """跨家族的第二条允许：判废对里「新增能力 ↔ 缺陷修复」的组合 0/38。"""
        from check_repo_theme import check

        derived = {"Home": {"链接", "分类", "标签", "筛选", "清除"}}
        items = [
            ("a[代码生成]", "新增收藏总览看板：把分类与标签整理成一块看板，点其中一块只看对应的那批。"),
            ("b[缺陷修复]", "标签点不动：列表里点标签没有反应，侧栏点同一个标签能筛出内容，两处对不上。"),
        ]
        result = check(items, derived=derived, max_unknown=99)
        self.assertTrue(
            not any(v["kind"].startswith("同主体") for v in result["violations"]),
            result["violations"],
        )
        self.assertEqual(result["module_counts"].get("Home"), 2, result["module_counts"])

    def test_mode_duplication_is_blocked(self):
        """跨主体但同模式也要拦（把每模式上限收到 1 时）：判废对里 15/38 是这种。"""
        from check_repo_theme import check

        derived = {
            "AlarmCenter": {"告警", "升级", "阈值"},
            "DeviceOffline": {"设备", "离线", "心跳"},
        }
        items = [
            ("a[代码生成]", "新增告警批量确认：多选若干条未确认的告警一次提交，逐条给出结果。"),
            ("b[代码生成]", "新增设备批量注册：多选若干台设备一次提交，逐台给出结果。"),
        ]
        result = check(items, derived=derived, max_unknown=99, max_per_mode=1)
        self.assertFalse(result["ok"], result["violations"])
        self.assertTrue(
            any(v["kind"] == "同模式重复" for v in result["violations"]), result["violations"]
        )

    def test_object_axis_blocks_same_subject_when_opt_in(self):
        """对象轴默认只当复核信号；显式加 --min-shared-object-words 2 才升级成硬拦。"""
        from check_repo_theme import check

        derived = {"DeadLinks": {"死链", "检测", "链接", "重试"}}
        items = [
            ("a[功能迭代]", "死链页面想补齐几种边界情形：检测中断以后已经判定的结果要保留，"
                             "单条超时可以单独重试，回到页面结论和计数要跟刚才一致。"),
            ("b[缺陷修复]", "死链检测断了以后已经查完的结果会丢，失败的那几条只给一句报错、"
                             "看不出是哪个网址，希望允许单独重试并且两处结论一致。"),
        ]
        default_result = check(items, derived=derived, max_unknown=99)
        self.assertNotIn(
            "同主体同对象", {v["kind"] for v in default_result["violations"]},
            "对象轴默认不该硬拦（噪声太大）",
        )
        strict_result = check(items, derived=derived, max_unknown=99,
                              min_shared_object_words=2)
        self.assertIn(
            "同主体同对象", {v["kind"] for v in strict_result["violations"]},
            "显式打开时对象轴要能拦下来",
        )


class MaxPerModeTest(unittest.TestCase):
    """写表闸门的每模式上限必须可透传：默认 3，显式传 2 才回到 24 条的旧口径。

    背景：容量 = min(单仓库上限 50, 主体数 x 2, 需求模式数 13 x 每模式上限)。
    需求模式词典 13 类，x 2 = 26 个模式席位 —— 不管仓库多大都到不了 30 条，
    所以默认值在 2026-09-22 由 2 放到 3（13 x 3 = 39 个席位），显式传 2 可以收回旧口径。
    """

    MODE_KEYS = {
        "新增展示视图": ("概览", "看板", "呈现"),
        "规则与阈值": ("阈值", "口径", "上限"),
        "状态流转": ("生命周期", "归档", "流转"),
        "权限与归属": ("越权", "只读", "受控"),
        "批量操作": ("批量", "多选", "整组"),
        "导入导出": ("导入", "导出", "打包"),
        "列表定位与筛选": ("翻页", "分页", "过滤"),
        "持久化与一致性": ("刷新", "重新进入", "保留原"),
        "边界与空态": ("空态", "暂无", "中断"),
        "缺陷处置": ("残留", "错位", "丢失"),
        "代码重构收拢": ("收拢", "抽取", "统一写法"),
        "工程化与流程": ("流水线", "部署", "本地开发"),
    }

    def _records(self, total: int) -> list[dict[str, str]]:
        from check_repo_theme import DEMAND_MODE_LEXICON

        usable = [mode for mode in DEMAND_MODE_LEXICON if mode != "代码理解"]
        rows = []
        for index in range(total):
            mode = usable[index % len(usable)]
            first, second, third = self.MODE_KEYS[mode]
            rows.append({
                # 工程化属于豁免类型，这样每条题面不需要落回真实主体，
                # 用例只考察「同模式重复」这一条闸门。
                "子文件夹名称": f"6600011{index:02d}-engineering-{index}",
                "任务类型": "工程化",
                "提示词": (f"模块{index:02d}的{first}页面希望增加新的{second}入口，"
                           f"{third}要按运维习惯说明清楚，第{index}条还要联动手册里的既有规则。"),
                "提示词类型": "主提示词",
                "备注": "",
            })
        return rows

    def _mode_violations(self, total: int, **kwargs) -> list[dict]:
        import os
        import tempfile

        from batch_prompt_workbook import run_write_gates

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            os.environ["SOLO_CREATE_REPO_LEDGER_ROOT"] = str(parent / "repo-ledgers")
            (parent / "repo-theme-ledger.json").write_text(
                json.dumps({"gate_version": "test", "entries": []}), encoding="utf-8",
            )
            gate = run_write_gates(parent, self._records(total), repo=None, **kwargs)
        return [item for item in (gate.get("theme", {}).get("violations") or [])
                if item.get("kind") == "同模式重复"]

    def test_default_limit_three_unlocks_thirty_plus(self):
        """默认口径（不传 max_per_mode）是 3：30 条、36 条通过，第 37 条被同模式重复拦下。"""
        self.assertFalse(self._mode_violations(30), "默认口径 3 下 30 条必须能写进工作簿")
        self.assertFalse(self._mode_violations(36))
        hits = self._mode_violations(37)
        self.assertTrue(hits, "默认口径下第 37 条必须被拦下")
        self.assertGreater(hits[0]["count"], 3)

    def test_explicit_limit_two_restores_the_old_ceiling(self):
        """显式传 2 可以收回旧口径：24 条通过，第 25 条被拦下。"""
        self.assertFalse(self._mode_violations(24, max_per_mode=2))
        hits = self._mode_violations(25, max_per_mode=2)
        self.assertTrue(hits, "显式口径 2 下第 25 条必须被拦下")
        self.assertGreater(hits[0]["count"], 2)

    def test_default_matches_check_repo_theme_constant(self):
        """默认值只有一处来源：check_repo_theme.DEFAULT_MAX_PER_MODE。"""
        from check_repo_theme import DEMAND_MODE_LEXICON, DEFAULT_MAX_PER_MODE, compute_capacity

        self.assertEqual(DEFAULT_MAX_PER_MODE, 3)
        derived = {f"s{i}": {"词"} for i in range(29)}
        capacity = compute_capacity(derived)
        self.assertEqual(capacity["mode_slots"], len(DEMAND_MODE_LEXICON) * 3)
        # 29 个主体 x 每主体 2 条 = 58，单仓库上限 50，模式席位 39 —— 卡在模式轴
        self.assertEqual(capacity["capacity"], len(DEMAND_MODE_LEXICON) * 3)


class DifficultyStructureTest(unittest.TestCase):
    def test_write_gate_blocks_without_ledger(self):
        """没有台账就不许把提示词写进工作簿：写入路径自己闸住，不靠人记得跑。"""
        import os
        import tempfile

        from batch_prompt_workbook import run_write_gates

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            os.environ["SOLO_CREATE_REPO_LEDGER_ROOT"] = str(parent / "repo-ledgers")
            records = [{
                "子文件夹名称": "66000111-codegen-1", "任务类型": "代码生成",
                "提示词": "新增录制列表分页：历史录制很多时按页浏览，翻页后保持原有顺序。",
                "提示词类型": "主提示词", "备注": "",
            }]
            gate = run_write_gates(parent, records, repo=None)
            self.assertFalse(gate["ok"])
            self.assertTrue(
                any("台账缺失" in item for item in gate["problems"]), gate["problems"]
            )

    def test_write_gate_blocks_subject_over_quota(self):
        """同一主体第 3 条、同一「主体 + 模式」第 2 条都要被写入闸拦下。"""
        import os
        import tempfile

        from batch_prompt_workbook import run_write_gates

        prompts = [
            "新增录制列表分页：历史录制很多时按页浏览，翻页后保持原有顺序。",
            "新增录制列表筛选：按通道过滤历史录制，找不到时给出空态提示。",
            "新增录制列表排序：把历史录制按名称排序，排序后仍能点开回看。",
            "新增录制列表收藏：把常用录制标记成收藏并排在最前面。",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            os.environ["SOLO_CREATE_REPO_LEDGER_ROOT"] = str(parent / "repo-ledgers")
            (parent / "repo-theme-ledger.json").write_text(
                json.dumps({"gate_version": "test", "entries": []}), encoding="utf-8",
            )
            # 造一个只含 RecordingPanel 的仓库，让四条题面都落到同一个模块上
            components = parent / "frontend" / "src" / "components"
            components.mkdir(parents=True)
            (components / "RecordingPanel.tsx").write_text(
                "const a = '历史录制'; const b = '开始录制'; const c = '删除录制记录';"
                "const d = '回看录制'; const e = '录制列表';",
                encoding="utf-8",
            )
            records = [
                {"子文件夹名称": f"6600011{i + 1}-codegen-{i + 1}", "任务类型": "代码生成",
                 "提示词": prompt, "提示词类型": "主提示词", "备注": ""}
                for i, prompt in enumerate(prompts)
            ]
            gate = run_write_gates(
                parent, records, repo=parent, max_per_module=3,
            )
            self.assertFalse(gate["ok"])
            self.assertTrue(
                any("主题去重未通过" in item for item in gate["problems"]),
                gate["problems"],
            )

    def test_write_gate_passes_and_records_gate_tag(self):
        """合法提示词要写得进去，并把生成规则版本落到备注与生成清单里。"""
        import os
        import tempfile

        from openpyxl import Workbook

        import batch_prompt_workbook as workbook_lib

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            os.environ["SOLO_CREATE_REPO_LEDGER_ROOT"] = str(parent / "repo-ledgers")
            workbook = parent / "solo-create-prompts.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "prompts"
            ws.append(list(workbook_lib.HEADERS))
            ws.append(["66000111-codegen-1", "代码生成", 1, "", "主提示词", "待生成", "", ""])
            wb.save(workbook)
            (parent / "repo-theme-ledger.json").write_text(
                json.dumps({"gate_version": "test", "entries": []}), encoding="utf-8",
            )
            result = workbook_lib.update(
                parent, None, "66000111-codegen-1",
                "新增告警处置预案：按告警类型维护可复用的处置步骤，逐条勾选后自动结单，"
                "没有值班人时回落到公共清单。",
                "", None, None, skip_version_check=True,
            )
            self.assertTrue(result["gate"]["ok"], result["gate"])
            self.assertTrue(result["manifest"])
            records = workbook_lib.read_workbook(workbook)
            self.assertIn("生成规则", str(records[0].get("备注") or ""))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["gate_ok"])
            self.assertTrue(manifest["gate_version"])

    def test_difficulty_default_covers_all_task_types(self):
        """难度检查默认覆盖全部任务类型：33 号是代码生成，只查缺陷修复会漏掉它。"""
        from check_prompt_difficulty import DEFAULT_TASK_TYPES

        self.assertEqual(DEFAULT_TASK_TYPES, "all")

    def test_non_defect_hits_go_to_needs_review_with_justification(self):
        """代码生成 / 功能迭代命中两项时进 needs_review，写清依据才算过。"""
        from check_prompt_difficulty import check

        prompt = "新增电极位置分布：按前额、额、中央、顶、枕把通道摆出来，点某个位置即切到该通道，鼠标停在上面给出名称。"
        label = "t[代码生成]"
        result = check([(label, prompt)], min_hits=2, defect_structure=True, defect_labels=set())
        self.assertTrue(result["needs_review"], result)
        self.assertTrue(result["ok"], "非缺陷题命中只进复核清单，不硬拦")

        justified = check(
            [(label, prompt)], min_hits=2, defect_structure=True, defect_labels=set(),
            justifications={label: "难度复核: 跨三个面板联动，另含回看与刷新两条路径"},
        )
        self.assertTrue(justified["ok"], justified)
        self.assertEqual(len(justified["justified"]), 1)

    def test_defect_task_still_hard_blocked(self):
        """缺陷修复题命中的照旧硬拦，别把复核机制当成通行证。"""
        from check_prompt_difficulty import check

        prompt = "修复导出按钮点不动，只改一处校验就够了。"
        label = "t[缺陷修复]"
        result = check([(label, prompt)], min_hits=2, defect_structure=True, defect_labels={label})
        self.assertFalse(result["ok"], result)

    def test_narrow_scope_is_rejected_even_with_single_hit(self):
        """「修改范围」的硬信号命中即拒：实测 41 号就栽在这条。

        弱信号（给出提示、弹一个提示这类空态文案）不算硬拒，否则好题会被大面积误伤。
        """
        from check_prompt_difficulty import check

        result = check(
            [("t", "在轨迹回放里把开始时间填得比结束时间晚，点查询仍然提示查询成功，期望只要改一处校验就够了。")],
            min_hits=2,
            defect_structure=False,
        )
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("修改范围" in reason for reason in result["violations"][0]["reasons"]))

        soft = check(
            [("t", "新增告警处置预案：按告警类型维护处置步骤，预案没覆盖的类型给出提示并允许手工填写。")],
            min_hits=2,
            defect_structure=False,
        )
        self.assertFalse(any("硬信号" in reason for record in soft["violations"] for reason in record["reasons"]))

    def test_defect_structure_requires_two_points_and_two_entries(self):
        from check_prompt_difficulty import check

        thin = check(
            [("t", "注册面板里把纬度清空后右侧面板直接报错，期望坐标不是数字时不允许提交。")],
            min_hits=2,
        )
        self.assertFalse(thin["ok"], "单点缺陷题必须被结构下限拦下")
        self.assertTrue(any("结构下限" in reason for reason in thin["violations"][0]["reasons"]))

        rich = check(
            [("t", "列表里同一台设备的告警被拆成两条重复显示，点开详情又发现计数对不上，"
                  "两处都来自同一份未清理的历史缓存；刷新页面后仍然对不上，切换班组再回来"
                  "重复条目还会多一条，失败时也没有任何提示。期望列表与详情用同一份数据同步更新，"
                  "刷新与切换后保持一致，异常时给出提示。")],
            min_hits=2,
        )
        record = rich["items"][0]
        self.assertTrue(record["defect_structure"]["enough_points"], record)
        self.assertTrue(record["defect_structure"]["enough_entries"], record)
        self.assertEqual(record["reasons"], [], record)

    def test_defect_structure_skipped_for_other_task_types(self):
        from check_prompt_difficulty import check

        result = check(
            [("t", "新增设备保养计划：按累计运行时长安排保养，临近到期的设备进入待保养清单。")],
            min_hits=2,
            defect_structure=False,
        )
        self.assertIsNone(result["items"][0]["defect_structure"])


if __name__ == "__main__":
    unittest.main()
