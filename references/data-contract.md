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

- 只有本次验收明确走“带 Session 提交态”时，才认一个“当前有效 `Trae Session ID`”。
- 进入“带 Session 提交态”后，这个“当前有效 `Trae Session ID`”必须同时等于 3 个位置的值：
  - 当前有效 `Trae Session ID` 的原始值。
  - 本次用于 `git commit` 的 message。
  - 最终质检结论里 `【Trae Session ID】` 输出的值。
- 进入“无 Session 只验收态”时，不做这 3 个位置的一致性校验，也不要为了凑齐它们去生成或代填值。
- 不允许对输入值做截断、清洗、重排、大小写改写或时间格式改写后再用于提交或输出。

## 1. 运行上下文 `run_context`

```text
{
  "route": "已投递验收路" | "首轮半严路" | "后续快路" | "批量建仓路" | "批量首轮路" | "批量投递路",
  "scope": "single_project" | "parent_batch",
  "phase": "route_selection" | "generation" | "delivery" | "acceptance" | "final_output",
  "is_acceptance": true | false,
  "browser_policy": "prefer_browser_allow_chrome_fallback" | "browser_only",
  "has_trae_session_id": true | false,
  "trae_session_id": "<当前上下文中的有效原始值>" | "",
  "acceptance_mode": "with_session_commit" | "without_session_review",
  "allowed_output_mode": "stop_only" | "prompt_only" | "batch_summary" | "acceptance_template"
}
```

字段约束：

- `route` 必须取 6 个合法 route 之一。
- `scope` 只能是 `single_project` 或 `parent_batch`。
- `phase` 只能取上面 5 个值，不要自造“review”“retry”之类的新阶段名。
- `browser_policy=prefer_browser_allow_chrome_fallback` 表示默认策略：优先 `@浏览器`，不可用时先降级 `@chrome`，只有 `@chrome` 也不可用时才允许再降级 Safari。
- `browser_policy=browser_only` 表示强制策略：只允许 `@浏览器`，不允许降级到 `@chrome` 或 Safari；如果当前线程未挂载 Browser 能力或 Browser 不可用，必须停止并要求切换到带 Browser 的新线程。
- 首轮提示词场景时，`allowed_output_mode` 必须是 `prompt_only`。
- `has_trae_session_id=true` 的前提是：来源为用户消息或当前上下文中的明确注入值，且格式匹配本文件 `0. Trae Session ID 格式`。
- `acceptance_mode=with_session_commit` 表示当前验收允许走提交 / push / 输出 `Commit ID` 与 `Trae Session ID` 的链路。
- `acceptance_mode=without_session_review` 表示当前验收只做结果判断，不做提交 / push，也不输出 `Commit ID` 与 `Trae Session ID`。

## 2. 单项目定位结果 `project_lookup`

这是 `batch_prompt_workbook.py locate-project --project .` 的语义格式：

```text
{
  "found": true | false,
  "folder": "<子文件夹名称>" | "",
  "task_type": "缺陷修复" | "代码生成" | "功能迭代" | "代码理解" | "代码重构" | "工程化" | "",
  "index": <整数> | null,
  "prompt": "<Excel 中提示词>" | "",
  "prompt_type": "主提示词" | "修复提示词" | "",
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
  "更新时间": "<时间字符串>" | ""
}
```

字段约束：

- Excel 固定列必须就是这 7 列，不要增删改列名。
- `编号` 必须按整数理解，不按聊天序号猜。
- `提示词` 为空表示还没生成。
- `提示词类型=主提示词` 表示首轮或主线提示词；`提示词类型=修复提示词` 表示只有在修复提示词真的进入验收时才追加的记录行。
- `状态=已发送` 表示该行提示词已经投递到 Trae。
- 进入验收后，不再往 `solo-create-prompts.xlsx` 写入任何验收结果、Repo URL、Commit ID、Trae Session ID、验收时间或不满意原因。
- `solo-create-prompts.xlsx` 只用于定位提示词、记录提示词类型，以及生成 / 投递阶段的状态维护。
- 验收结果必须通过 `$solo-acceptance-results` 写入项目目录父级的独立结果 Excel，默认文件名为 `solo-create-acceptance-results2.xlsx`。

## 4. 验收上下文 `acceptance_context`

只要进入验收，就必须在内部按下面格式收束上下文：

```text
{
  "repo_url": "https://github.com/owner/repo" | "无",
  "commit_id": "<40位提交哈希>" | "无",
  "trae_session_id": "<当前上下文中的有效原始值>" | "",
  "prompt_text": "<本次验收对应提示词>",
  "modified_file_count": <整数>,
  "completion": "已完成" | "未完成" | "暂时无法判定完成",
  "acceptance_mode": "with_session_commit" | "without_session_review",
  "needs_dissatisfaction": true | false,
  "needs_next_prompt": true | false
}
```

字段约束：

- `acceptance_mode=with_session_commit` 且 `completion=已完成` 时，`commit_id` 不能是 `无`。
- `completion=已完成` 时，`needs_dissatisfaction=false` 且 `needs_next_prompt=false`。
- `completion!=已完成` 时，`needs_dissatisfaction=true` 且 `needs_next_prompt=true`。
- `prompt_text` 必须对应当前这次验收的提示词；修复验收时不要回填首轮主提示词。
- `modified_file_count` 必须是当前这次验收实际涉及的修改文件个数，按整数输出，最小值为 `0`。
- `modified_file_count` 的统计口径必须前后一致：优先按当前工作区里围绕本次提示词的已修改文件集合统计；如果当前是 `带 Session 提交态` 且改动已经提交，也可以按本次被验收提交对应的文件集合统计，但最终输出只允许保留一个整数值。
- `acceptance_mode=with_session_commit` 时，`trae_session_id` 必须是当前上下文里的有效原始值，且来源必须是用户消息或明确注入值。
- `acceptance_mode=with_session_commit` 时，`trae_session_id` 还必须与本次 `git commit` message 以及最终输出里的 `Trae Session ID` 字段完全一致。
- `acceptance_mode=without_session_review` 时，`trae_session_id` 必须为空字符串，`commit_id` 允许为 `无`，且不要进入提交 / push 链路。
- `repo_url` 必须规范化为不带 `.git` 后缀的 GitHub HTTPS 页面 URL，格式为 `https://github.com/owner/repo`；无法取得时才写 `无`。
- 如果存在 push / remote 阻塞且当前无法恢复正常推送，只在 `acceptance_mode=with_session_commit` 下阻塞 `acceptance_output`。

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
- `产物不满意` 无法判断时也必须写 30 个字以上，说明当前缺少用户可见证据；不要使用短句 `暂没法判断。`
- 两个 value 都必须至少 30 个字，并以标点结尾。
- `过程不满意` 必须同时具备具体环节 / 步骤 / 工具调用 / 文件改动、实际行为、证据和业务影响。
- `过程不满意` 必须落在模型自身的指令遵循、规划、工具使用、幻觉、验证或纠错问题上，不能只是代码现象平移，也不能归因于环境 / 网络波动。
- `产物不满意` 必须定位到具体功能、页面区域、交互、测试行为、配置行为或交付物，并写清客观证据或明确需求缺口。
- `过程不满意`、`产物不满意` 和 `下一轮提示词` 的最终文本都必须是中文自然描述，不能出现文件路径、文件名、函数名、变量名、接口名、配置名、命令名、英文工具名、英文单词或原始代码符号；技术证据只允许作为传给 `$solo-dissatisfaction` 的内部输入，由其转换成中文功能描述。
- `产物不满意` 不能只写 `效果不好`、`不行`、`一般`、`代码有 bug`、`没完成任务` 这类笼统表面症状。
- `过程不满意`、`产物不满意` 不能互相改写。
- `过程不满意`、`产物不满意` 和 `下一轮提示词` 必须由 `$solo-dissatisfaction` 生成，并通过 `/Users/fengyin/.codex/skills/solo-dissatisfaction/scripts/validate_dissatisfaction.py` 校验后才能进入最终输出。

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
  "Commit ID": "<40位提交哈希>" | "无" | null,
  "Trae Session ID": "<当前上下文中的有效原始值>" | null,
  "提示词": "<当前验收对应提示词>",
  "修改文件个数": <整数>,
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
- 命中 `Hard Stops` 的 `2.6` 时，只在 `acceptance_mode=with_session_commit` 下不能输出 `acceptance_output`；必须先告知 push / remote 问题。
- `修改文件个数` 必须紧跟在 `提示词` 后面输出。
- `任务完成情况` 必须带句号，并且只允许 3 个固定值。
- `acceptance_mode=with_session_commit` 时，`Trae Session ID` 字段必须原样输出通过格式校验的原始值，不要截断，不要改写。
- `acceptance_mode=with_session_commit` 时，`Trae Session ID` 字段还必须与当前有效原始值、以及本次 `git commit` message 完全一致。
- `acceptance_mode=without_session_review` 时，最终输出里不要展示 `Commit ID` 和 `Trae Session ID`。
- `Repo URL` 必须规范化为不带 `.git` 后缀的 GitHub HTTPS 页面 URL，格式为 `https://github.com/owner/repo`。

## 7. 验收结果 Excel 行 `acceptance_result_row`

只要输出 `acceptance_output`，就必须在最终回复前通过 `$solo-acceptance-results` 把同一份验收结果回填到独立结果 Excel。该行按下面格式理解：

```text
{
  "Repo ID": "ybl-<数字编号>-<序号>",
  "Trae Session ID": "<当前上下文中的有效原始值>" | "",
  "提示词": "<当前验收对应提示词>",
  "Repo URL": "https://github.com/owner/repo" | "无",
  "Commit ID": "<40位提交哈希>" | "",
  "任务类型": "0-1代码生成" | "Bug修复" | "Feature迭代" | "代码理解" | "代码重构" | "工程化",
  "业务领域": "Web前端" | "<明确输入值>",
  "修改范围": "跨模块多文件" | "<明确输入值>",
  "任务难度": "困难" | "<明确输入值>",
  "任务是否完成": "已完成" | "未完成" | "暂时无法判定完成",
  "过程与产物是否满意": "满意" | "不满意",
  "不满意原因": "过程不满意：...\n产物不满意：..." | ""
}
```

字段约束：

- 结果 Excel 与 `solo-create-prompts.xlsx` 必须分离。
- 结果 Excel 默认放在项目目录父级，默认文件名为 `solo-create-acceptance-results2.xlsx`。
- 如果结果 Excel 不存在，第一次回填时必须新建。
- 结果 Excel 的 sheet 名必须是 `prompts`。
- 表头必须严格是这 12 列，顺序不能改：`Repo ID`、`Trae Session ID`、`提示词`、`Repo URL`、`Commit ID`、`任务类型`、`业务领域`、`修改范围`、`任务难度`、`任务是否完成`、`过程与产物是否满意`、`不满意原因`。
- `Repo ID` 必须匹配 `^ybl-[0-9]+-[0-9]+$`；如果无法从当前项目目录名解析出标准值，必须显式传入标准 `repo_id`，禁止把目录名或非标准编号写入结果 Excel。
- `acceptance_mode=without_session_review` 时，最终聊天输出不展示 `Commit ID` 和 `Trae Session ID`，但回填 Excel 时这两列必须存在，值写空字符串。
- `Repo URL` 必须与最终聊天输出一致，写入前也必须规范化为不带 `.git` 后缀的 GitHub HTTPS 页面 URL。
- `completion=已完成` 时，`过程与产物是否满意=满意`，`不满意原因` 为空。
- `completion!=已完成` 时，`过程与产物是否满意=不满意`，`不满意原因` 只包含 `过程不满意` 和 `产物不满意`，不要写入 `下一轮提示词`；回填输入必须额外携带 `next_prompt`、`process_evidence` 或 `process_trace_evidence`、`product_evidence` 或 `artifact_evidence`、`model_fault_basis`、`environment_issue_excluded=true`，供回填脚本再次运行 `$solo-dissatisfaction` 校验。
- `$solo-acceptance-results` 返回 `ok: true` 后，才允许结束验收流程。
