"""出题侧两个闸门的回归用例（2026-09-16 按平台规则 C 与 P3 判词补）。

背景：cc-6600009 那批 48 条里 21 条按规则 C（同仓库语义雷同）判废弃、1 条按 P3（题目过于简单）
判废弃，两条判词都写「不可返修，只改写措辞无效」。这里把当时的口径固化成用例。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class RepoThemeTest(unittest.TestCase):
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


class DifficultyStructureTest(unittest.TestCase):
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
