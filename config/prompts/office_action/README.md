# office_action 提示词方案 — 中文审查意见通知书撰写

把 skill 文档 `patent-office-action-cn-new.md` 的撰写方法论整合为一套可直接发给 AI 的提示词。面向 **DeepSeek**（V4 Flash，1M 上下文 / 384K 输出，足以容纳本申请 + 对比文件全文）。

## 文件

| 文件 | 作用 |
|------|------|
| `system.txt` | 固定指令：角色、4 条核心规则（铁律）、输入检查、撰写流程（三步法 + 单/双对比文件双模式）、版式规范、交付前校验 |
| `user.txt` | 每次任务的可变输入模板：本申请、D1、D2、评述模式/角色指定、输出要求 |

提示词完全自包含，**不依赖任何模板文件**（skill 明确"不含模板文件，DOCX 从零生成"）。

## 使用方式

### 方式一：项目内调用

GUI 主界面点 **「📝 撰写通知书」** → 选对比文件（当前检索结果 / 历史运行 / 上传 PDF）→ 确认 D1/D2 角色与单/双对比文件模式 → 生成。

代码层（`src/analysis/oa_writer.py`）：

```python
from src.utils.config import Settings
from src.ai_client import AIClient

settings = Settings()
client = AIClient(settings)
system = settings.get_prompt_text("office_action", "system")
# user 由 OAWriter.write() 按本申请 + 对比文件自动组装
out = client.chat(system, user, max_tokens=16384)
```

### 方式二：网页端 / 手动发送

把 `system.txt` 全文作为第一条消息（系统设定），再把填好材料的 `user.txt` 作为第二条消息发送。

## 关键设计

1. **4 条核心规则前置为铁律**：技术特征逐一对比、禁止编造、公开日核查、尊重指定主线——用强否定句式，减少 DeepSeek 长指令下虚构段号/公开内容的风险。
2. **单/双对比文件双模式**：新 skill 明确两种收束路径（D2 公开区别特征 / 公知常识或固有属性），提示词完整保留。
3. **结尾段两种情形**：全部无创造性 vs 部分具备创造性（提示并入附加技术特征）。
4. **DOCX 从零生成**：`src/analysis/oa_docx.py` 按版式规范生成（A4 / 页边距上下2.54cm左右3.17cm / 宋体12磅 / 两端对齐 / 首行缩进2字符 / 行距最小值20磅 / 标题加粗 / 化学式真实下标），不依赖任何模板文件。

## 注意事项

- 若 `patent-office-action-cn-new.md` 更新，需同步修改 `system.txt` 和 `user.txt`。
- 生成结果务必人工复核引用核验（禁止编造规则）——AI 的自校验是辅助，最终以对比文件原文为准。
