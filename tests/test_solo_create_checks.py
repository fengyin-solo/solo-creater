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
    def test_repo_modules_and_feature_points_block_repeat(self):
        """按仓库结构派生模块 + 功能点级去重：同一模块同一能力只能出 1 条。

        cc-6600011 那批 17 条规则 C 废弃里，多数就是同模块同能力的第二条题
        （列表翻页对列表过滤、占比口径对参考范围、走势曲线对越线预警）。
        """
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

            first = "新增录制列表分页：录制很多时按页浏览，翻页后保持当前通道的录制顺序。"
            second = "新增录制列表排序：把历史录制按名称排序，排序后仍能点开回看。"
            result = check([("a", first), ("b", second)], derived=derived, max_per_module=3)
            self.assertFalse(result["ok"], result["violations"])
            self.assertTrue(
                any(v["kind"] == "功能点重复" for v in result["violations"]), result["violations"]
            )

    def test_unknown_module_is_a_violation(self):
        """题面落不回仓库模块时要拦：不认模块就查不出同功能点重复。"""
        from check_repo_theme import check

        result = check(
            [("a", "新增一件小事：把标题改一下。")],
            derived={"RecordingPanel": {"录制", "回放"}},
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any(v["kind"] == "模块未识别" for v in result["violations"]), result["violations"])

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

    def test_module_quota_blocks_overcrowded_module(self):
        """同一模块超过上限就要硬拦：实测告警中心出了 15 条，是废弃的主因。"""
        from check_repo_theme import check

        items = [
            ("t1", "新增告警自动分派：按设备所属区域把新告警指派给对应人员，没人值班时回落到公共清单。"),
            ("t2", "新增告警处理时效视图：按处理人统计告警从产生到确认的耗时，标出明显超时的条目。"),
            ("t3", "新增告警处置预案：按告警类型维护可复用的处置步骤，逐条勾选后自动结单。"),
            ("t4", "新增告警交接：把尚未确认的告警连同处理进度转交给下一班，接班人看到接手时间。"),
        ]
        result = check(items, max_per_module=3)
        self.assertFalse(result["ok"])
        self.assertTrue(any(v["kind"] == "模块超额" for v in result["violations"]), result["violations"])
        self.assertEqual(result["module_counts"].get("告警中心"), 4)

    def test_module_quota_passes_diverse_batch(self):
        from check_repo_theme import check

        items = [
            ("t1", "新增告警处置预案：按告警类型维护可复用的处置步骤，逐条勾选后自动结单。"),
            ("t2", "新增围栏准入名单：设备越过不在名单内的围栏时生成一条闯入记录。"),
            ("t3", "新增运行数据导出：把选定时间段内的设备清单与告警明细打包导出。"),
            ("t4", "新增轨迹接续回放：同一天的多段行程接续播放，中途暂停后可以继续。"),
        ]
        result = check(items, max_per_module=3)
        self.assertTrue(result["ok"], result["violations"])

    def test_same_module_same_capability_becomes_candidate(self):
        """同模块同能力不硬拦，但必须进候选清单让出题人换题材。"""
        from check_repo_theme import check

        items = [
            ("a", "新增告警升级处理：超过设定时长仍未被确认的告警自动提升级别。"),
            ("b", "新增告警超时归档：超过保留期的告警自动转入归档区，只显示近期内容。"),
        ]
        result = check(items, max_per_module=3)
        self.assertTrue(result["candidate_pairs"], "同模块同能力必须进候选清单")
        pair = result["candidate_pairs"][0]
        self.assertIn("告警中心", pair["modules"])
        self.assertIn("新增处理机制", pair["capabilities"])

    def test_ledger_repeating_current_batch_is_not_counted_twice(self):
        """复查同一批时，台账里装的就是这批自己的记录，不能再算一遍。

        2026-09-16 实测 cc-9900003：写完台账后原地复查，每个模块的 3 条被算成 6 条，
        凭空报出 40 处「功能点重复」与 14 处「模块超额」，把整批题判成不通过。
        """
        from check_repo_theme import check

        items = [
            ("t1", "新增告警处置预案：按告警类型维护可复用的处置步骤，逐条勾选后自动结单。"),
            ("t2", "新增围栏准入名单：设备越过不在名单内的围栏时生成一条闯入记录。"),
        ]
        # 台账内容与当前这批完全相同（写入台账后立即复查就是这个状态）
        ledger = {
            "entries": [
                {"label": "t1", "module": "告警中心", "feature_point": "告警中心|新增处理机制"},
                {"label": "t2", "module": "围栏工作台", "feature_point": "围栏工作台|新增处理机制"},
            ]
        }
        result = check(items, max_per_module=3, base_ledger=ledger)
        self.assertEqual(result["module_counts"].get("告警中心"), 1, result["module_counts"])
        self.assertEqual(result["module_counts"].get("围栏工作台"), 1, result["module_counts"])
        self.assertEqual(result["violations"], [])
        self.assertTrue(result["ok"])

        # 台账里是别的批次的记录时，仍然要算进配额
        other_ledger = {
            "entries": [
                {"label": "old-1", "module": "告警中心", "feature_point": "告警中心|新增入口与共享"},
                {"label": "old-2", "module": "告警中心", "feature_point": "告警中心|新增清单流程"},
            ]
        }
        result = check(items, max_per_module=3, base_ledger=other_ledger)
        self.assertEqual(result["module_counts"].get("告警中心"), 3, result["module_counts"])


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

    def test_write_gate_blocks_module_over_quota_and_duplicate_feature_point(self):
        """同一模块第 4 条、同一功能点第 2 条都要被写入闸拦下。"""
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
                any("模块超额" in item or "功能点重复" in item for item in gate["problems"]),
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
