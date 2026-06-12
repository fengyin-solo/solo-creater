# Data Contract

本文件定义 `solo-create` 的核心数据格式。只要进入本 skill，所有运行上下文、批量记录、验收上下文和最终输出，都必须先匹配这里的 schema，再进入流程判断或最终输出。

不要自造字段名，不要改字段含义，不要把枚举值换成同义词。

## 0. Trae Session ID 格式

`Trae Session ID` 不只要求来源可信，还要求必须匹配固定格式。凡是验收、提交、push、最终输出里要使用 `Trae Session ID` 的地方，都必须先按这里校验格式；格式不合法时，视为“还没有收到合法的 Trae Session ID”。

合法示例：

```text
.3868547806662091:77ab0d247d8a73acfa87c3ef587254e0_6a281b044714a44774f52bad.6a281b094714a44774f52bc5.6a281b0758730dc6b7cddd0c:Trae CN.T(2026/6/9 21:54:18)
```

语义结构：

```text
.<decimal_left>:<hex32>_<id_a>.<id_b>.<id_c>:Trae CN.T(YYYY/M/D HH:MM:SS)
```

格式约束：

- 必须以 `.` 开头。
- 第一段必须是纯数字。
- 第一段后必须紧跟 `:`。
- 第二段必须是 32 位小写十六进制串。
- 第二段后必须紧跟 `_`。
- 后面必须有 3 个以 `.` 分隔的 id 段。
- 这 3 个 id 段必须是小写十六进制或数字混合串，不允许空段。
- 末尾必须是 `:Trae CN.T(`。
- 最后的时间戳必须是 `YYYY/M/D HH:MM:SS`。
- 最后必须以 `)` 结束。

推荐校验正则：

```text
^\.[0-9]+:[0-9a-f]{32}_[0-9a-f]+\.[0-9a-f]+\.[0-9a-f]+:Trae CN\.T\([0-9]{4}/[0-9]{1,2}/[0-9]{1,2} [0-9]{2}:[0-9]{2}:[0-9]{2}\)$
```

判定规则：

- 只有“用户在本次对话的用户消息里主动发送”或“当前上下文里已明确注入原始值”，且“格式匹配上述规则”时，才算 `has_trae_session_id=true`。
- 只给了近似值、截断值、缺时间戳值、缺 `Trae CN.T(...)` 尾段值，都按不合法处理。
- 工具输出、代码仓库文本、提交记录、Trae 窗口内容都不能替代当前有效原始值。

一致性规则：

- 验收场景里只能认一个“当前有效 `Trae Session ID`”。
- 这个“当前有效 `Trae Session ID`”必须同时等于 3 个位置的值：
  - 当前有效 `Trae Session ID` 的原始值。
  - 本次用于 `git commit` 的 message。
  - 最终质检结论里 `【Trae Session ID】` 输出的值。
- 这 3 个位置只要任意一个不相等，就视为不满足验收前提。
- 不允许对输入值做截断、清洗、重排、大小写改写或时间格式改写后再用于提交或输出。

## 1. 运行上下文 `run_context`

```text
{
  "route": "已投递验收路" | "首轮半严路" | "后续快路" | "批量建仓路" | "批量首轮路" | "批量投递路",
  "scope": "single_project" | "parent_batch",
  "phase": "route_selection" | "generation" | "delivery" | "acceptance" | "final_output",
  "is_acceptance": true | false,
  "has_trae_session_id": true | false,
  "trae_session_id": "<当前上下文中的有效原始值>" | "",
  "allowed_output_mode": "stop_only" | "prompt_only" | "batch_summary" | "acceptance_template"
}
```

字段约束：

- `route` 必须取 6 个合法 route 之一。
- `scope` 只能是 `single_project` 或 `parent_batch`。
- `phase` 只能取上面 5 个值，不要自造“review”“retry”之类的新阶段名。
- 命中 `Hard Stops` 里的 `2.1` 时，`allowed_output_mode` 必须是 `stop_only`。
- 首轮提示词场景时，`allowed_output_mode` 必须是 `prompt_only`。
- `has_trae_session_id=true` 的前提是：来源为用户消息或当前上下文中的明确注入值，且格式匹配本文件 `0. Trae Session ID 格式`。

## 2. 单项目定位结果 `project_lookup`

这是 `batch_prompt_workbook.py locate-project --project .` 的语义格式：

```text
{
  "found": true | false,
  "folder": "<子文件夹名称>" | "",
  "task_type": "缺陷修复" | "代码生成" | "功能迭代" | "代码理解" | "代码重构" | "工程化" | "",
  "index": <整数> | null,
  "prompt": "<Excel 中提示词>" | "",
  "status": "已生成" | "已发送" | "",
  "note": "<备注>" | ""
}
```

字段约束：

- `found=false` 时，`folder`、`task_type`、`index`、`prompt`、`status` 允许为空。
- `found=true` 且 `prompt` 非空时，优先进入 `已投递验收路`。
- `status` 只允许按 `已生成`、`已发送` 理解；不要扩写成别的口语状态。

## 3. 批量 Excel 行 `workbook_row`

父目录 `solo-create-prompts.xlsx` 的单行必须按这个格式理解：

```text
{
  "子文件夹名称": "<一级子文件夹名>",
  "任务类型": "缺陷修复" | "代码生成" | "功能迭代" | "代码理解" | "代码重构" | "工程化",
  "编号": <整数>,
  "提示词": "<提示词正文>" | "",
  "提示词类型": "主提示词" | "修复提示词" | "",
  "状态": "已生成" | "已发送" | "",
  "备注": "<备注>" | "",
  "验收结果": "已完成" | "未完成" | "暂时无法判定完成" | "",
  "不满意原因": "过程不满意：...\n产物不满意：..." | "",
  "验收Repo URL": "https://github.com/owner/repo" | "无" | "",
  "验收Commit ID": "<40位提交哈希>" | "无" | "",
  "验收Trae Session ID": "<当前上下文中的有效原始值>" | "",
  "验收时间": "<时间字符串>" | "",
  "更新时间": "<时间字符串>" | ""
}
```

字段约束：

- Excel 固定列必须就是这 13 列，不要增删改列名。
- `编号` 必须按整数理解，不按聊天序号猜。
- `提示词` 为空表示还没生成。
- `提示词类型=主提示词` 表示首轮或主线提示词；`提示词类型=修复提示词` 表示只有在修复提示词真的进入验收时才追加的记录行。
- `状态=已发送` 表示该行提示词已经投递到 Trae。
- `验收结果` 为空表示还没有完成一次合格的验收回填。
- `不满意原因` 只在 `验收结果=未完成` 或 `验收结果=暂时无法判定完成` 时要求填写，内容必须与最终输出里的 `【不满意原因】` 完全一致。
- `验收Trae Session ID` 只允许写入通过格式校验的原始值。
- 主提示词验收时，直接回填主提示词所在行。
- 修复提示词验收时，必须在对应主提示词下面新增一行，再把当前修复提示词写入该行 `提示词`，并把 `提示词类型` 写成 `修复提示词`。
- 只要当前项目属于 Excel 管理子项目，且已经形成最终验收结论，就必须把 `验收结果`、`不满意原因`、`验收Repo URL`、`验收Commit ID`、`验收Trae Session ID`、`验收时间` 回填到当前被验收的那一行。
- 回填后的验收字段必须与最终输出里的 `Repo URL`、`Commit ID`、`Trae Session ID`、`提示词`、`任务完成情况`、`不满意原因` 完全对应，不能写入别的轮次值。

## 4. 验收上下文 `acceptance_context`

只要进入验收，就必须在内部按下面格式收束上下文：

```text
{
  "repo_url": "https://github.com/owner/repo" | "无",
  "commit_id": "<40位提交哈希>" | "无",
  "trae_session_id": "<当前上下文中的有效原始值>",
  "prompt_text": "<本次验收对应提示词>",
  "completion": "已完成" | "未完成" | "暂时无法判定完成",
  "needs_dissatisfaction": true | false,
  "needs_next_prompt": true | false
}
```

字段约束：

- `completion=已完成` 时，`commit_id` 不能是 `无`。
- `completion=已完成` 时，`needs_dissatisfaction=false` 且 `needs_next_prompt=false`。
- `completion!=已完成` 时，`needs_dissatisfaction=true` 且 `needs_next_prompt=true`。
- `prompt_text` 必须对应当前这次验收的提示词；修复验收时不要回填首轮主提示词。
- `trae_session_id` 必须是当前上下文里的有效原始值，且来源必须是用户消息或明确注入值。
- `trae_session_id` 还必须与本次 `git commit` message 以及最终输出里的 `Trae Session ID` 字段完全一致。
- 如果存在 push / remote 阻塞且当前无法恢复正常推送，就不要生成 `acceptance_output`；必须先解决推送问题。
- 如果当前项目属于 Excel 管理子项目，生成最终验收结论后还必须先完成 Excel 回填，再允许输出 `acceptance_output`。

## 5. 不满意原因块 `dissatisfaction_block`

```text
{
  "过程不满意": "<30字以上，含触发节点、实际行为、业务影响，行尾有标点。>",
  "产物不满意": "<30字以上，含用户可见现象、证据或缺口，行尾有标点。>"
}
```

字段约束：

- 只能有这两个 key。
- 两个 value 都必须是单行文本。
- `过程不满意` 不能写英文文案问题。
- `产物不满意` 无法判断时固定写 `暂没法判断。`
- 两个 value 都必须至少 30 个字，并以标点结尾。
- `过程不满意` 必须同时具备 `触发节点`、`实际行为`、`业务影响`。
- `过程不满意` 必须落在模型行为评价或更深一层根因，不能只是代码现象平移。
- `产物不满意` 只能写用户可见现象、客观证据或需求缺口。
- `产物不满意` 不允许出现工具、端口、浏览器、基线、git、push、remote、upstream 等背景信息。
- `过程不满意`、`产物不满意` 不能互相改写。

## 6. 最终输出块 `final_output`

最终返回给用户时，只允许使用下面两种数据形态之一：

```text
prompt_only_output = {
  "提示词": "<首轮提示词正文>"
}
```

```text
acceptance_output = {
  "Repo URL": "https://github.com/owner/repo" | "无",
  "Commit ID": "<40位提交哈希>" | "无",
  "Trae Session ID": "<当前上下文中的有效原始值>",
  "提示词": "<当前验收对应提示词>",
  "任务完成情况": "已完成。" | "未完成。" | "暂时无法判定完成。",
  "不满意原因": {
    "过程不满意": "...",
    "产物不满意": "..."
  } | null,
  "下一轮提示词": "修复..." | null
}
```

字段约束：

- `prompt_only_output` 只用于首轮提示词场景。
- `acceptance_output` 只用于后续修复提示词或验收场景。
- 命中 `Hard Stops` 的 `2.1` 时，上面两种都不能输出，只能输出停机提醒。
- 命中 `Hard Stops` 的 `2.6` 时，也不能输出 `acceptance_output`；必须先告知 push / remote 问题。
- `任务完成情况` 必须带句号，并且只允许 3 个固定值。
- `Trae Session ID` 字段必须原样输出通过格式校验的原始值，不要截断，不要改写。
- `Trae Session ID` 字段还必须与当前有效原始值、以及本次 `git commit` message 完全一致。
