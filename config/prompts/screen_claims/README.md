# Claims 广筛（screen_claims）

只发送候选专利的权利要求和/或具体实施方式，按字符预算自适应分批，对全部候选 AI 评分排序。有三种内容模式（`analysis.screen_content` 配置）：`claims` / `embodiments` / `claims+embodiments`，分别对应三个 system 文件。

## 文件结构

| 文件 | 内容模式 | 对应配置值 |
|---|---|---|
| `system_claims.txt` | 仅权利要求 | `claims` |
| `system_embodiments.txt` | 仅具体实施方式 | `embodiments` |
| `system_both.txt` | 权利要求 + 具体实施方式 | `claims+embodiments` |
| `user.txt` | 共享任务模板 | — |

## 占位符

| 占位符 | 说明 |
|---|---|
| `{total}` | 本批对比文件篇数（system 文件） |
| `{patent_summary}` | 本申请概要（按 `screen_content` 模式感知，见下） |
| `{batch_number}` | 当前批次号（从 1 开始） |
| `{num_batches}` | 总批次数 |
| `{batch_count}` | 本批对比文件篇数 |
| `{candidates_text}` | 本批候选列表文本 |

## 本申请概要（`{patent_summary}`）模式

本申请概要跟随 `analysis.screen_content` 模式，与对比文件侧内容对齐：

| 模式 | 本申请概要内容 | 具体实施方式预算 |
|---|---|---|
| `claims` | 发明名称、IPC、摘要、全量权利要求 | 无 |
| `embodiments` | 发明名称、IPC、摘要、具体实施方式 | `screen_claims_limit`（给足） |
| `claims+embodiments` | 发明名称、IPC、摘要、全量权利要求、具体实施方式 | `screen_claims_limit` 的一半 |

> 注意：system 文件中 JSON 输出示例的字面量花括号 `{}` 会原样保留，无需转义。
