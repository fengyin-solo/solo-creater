# Data Contract

本文件定义 `solo-create` 的核心数据格式。只要进入本 skill，所有运行上下文、批量记录、验收上下文和最终输出，都必须先匹配这里的 schema，再进入流程判断或最终输出。

不要自造字段名，不要改字段含义，不要把枚举值换成同义词。

## 0. Trae Session ID 格式

`Trae Session ID` 不只要求“用户在本次对话里主动发送”，还要求必须匹配固定格式。凡是验收、提交、push、最终输出里要使用 `Trae Session ID` 的地方，都必须先按这里校验格式；格式不合法时，视为“还没有收到合法的 Trae Session ID”。

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

- 只有“用户本次对话里主动发送”且“格式匹配上述规则”时，才算 `has_trae_session_id=true`。
- 只给了近似值、截断值、缺时间戳值、缺 `Trae CN.T(...)` 尾段值，都按不合法处理。

一致性规则：

- 验收场景里只能认一个“当前有效 `Trae Session ID`”。
- 这个“当前有效 `Trae Session ID`”必须同时等于 3 个位置的值：
  - 用户本次对话里输入的原始值。
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
  "trae_session_id": "<用户本次对话原值>" | "",
  "allowed_output_mode": "stop_only" | "prompt_only" | "batch_summary" | "acceptance_template"
}
```

字段约束：

- `route` 必须取 6 个合法 route 之一。
- `scope` 只能是 `single_project` 或 `parent_batch`。
- `phase` 只能取上面 5 个值，不要自造“review”“retry”之类的新阶段名。
- 命中 `Hard Stops` 里的 `2.1` 时，`allowed_output_mode` 必须是 `stop_only`。
- 首轮提示词场景时，`allowed_output_mode` 必须是 `prompt_only`。
- `has_trae_session_id=true` 的前提是：用户本次对话里主动发送，且格式匹配本文件 `0. Trae Session ID 格式`。

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
  "状态": "已生成" | "已发送" | "",
  "备注": "<备注>" | "",
  "更新时间": "<时间字符串>" | ""
}
```

字段约束：

- Excel 固定列必须就是这 7 列，不要增删改列名。
- `编号` 必须按整数理解，不按聊天序号猜。
- `提示词` 为空表示还没生成。
- `状态=已发送` 表示该行提示词已经投递到 Trae。

## 4. 验收上下文 `acceptance_context`

只要进入验收，就必须在内部按下面格式收束上下文：

```text
{
  "repo_url": "https://github.com/owner/repo" | "无",
  "commit_id": "<40位提交哈希>" | "无",
  "trae_session_id": "<用户本次对话原值>",
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
- `trae_session_id` 必须是用户本次对话里主动发送且格式合法的原始值。
- `trae_session_id` 还必须与本次 `git commit` message 以及最终输出里的 `Trae Session ID` 字段完全一致。

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
  "Trae Session ID": "<用户本次对话原值>",
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
- `任务完成情况` 必须带句号，并且只允许 3 个固定值。
- `Trae Session ID` 字段必须原样输出通过格式校验的原始值，不要截断，不要改写。
- `Trae Session ID` 字段还必须与用户输入值、以及本次 `git commit` message 完全一致。
