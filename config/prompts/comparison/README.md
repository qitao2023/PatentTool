# 新颖性/创造性对比（comparison）

对单个对比文献与本申请做详细对比分析，输出结构化 JSON（相关度评分、新颖性/创造性影响、相同/不同技术特征、综合评述）。

## 占位符（user.txt）

| 占位符 | 说明 |
|---|---|
| `{application_title}` / `{application_ipc}` / `{application_abstract}` | 本申请发明名称 / IPC 分类 / 摘要 |
| `{application_claims}` | 本申请权利要求书（前 10 条，换行分隔） |
| `{application_description}` | 本申请说明书节选（前 1500 字） |
| `{comparison_publication_number}` / `{comparison_title}` / `{comparison_applicant}` | 对比文献公布号 / 标题 / 申请人 |
| `{comparison_publication_date}` / `{comparison_ipc}` / `{comparison_abstract}` | 对比文献公开日 / IPC / 摘要 |
| `{comparison_claims}` / `{comparison_description}` | 对比文献权利要求书 / 说明书节选 |

> 注意：`user.txt` 中 JSON 输出格式示例的字面量花括号 `{}` 会原样保留，无需转义。
