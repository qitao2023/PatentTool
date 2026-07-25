# 专利检索分析工具 (Patent Tool)

AI驱动的Windows桌面工具，用于专利PDF检索、对比分析和撰写。

## 功能

1. **PDF解析** — 读取中文/英文专利PDF，提取权利要求、摘要、说明书
2. **AI检索式生成** — Claude API理解专利技术方案，生成HimmPat指令检索式（≤10个）
3. **HimmPat自动检索** — Playwright模拟人操作，登录HimmPat并逐条检索（最多前50个结果）
4. **去重管理** — 按公开号+标题相似度跨检索式去重
5. **对比分析** — AI对比原始专利与检索结果，评估新颖性和特征重叠
6. **报告导出** — 生成HTML/Word格式的对比分析报告

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装Playwright浏览器
playwright install chromium

# 3. 配置API Key
cp config/.env.example config/.env
# 编辑 config/.env，填入 Anthropic API Key

# 4. 启动软件
python -m src.main
```

## 使用

1. 在软件界面中点击「浏览」选择专利PDF文件
2. 设置检索参数（最多检索式数、每式结果数）
3. 点击「开始分析」
4. 首次使用需在打开的浏览器中手动登录HimmPat
5. 等待自动检索和分析完成
6. 查看对比分析报告

## 技术栈

- GUI: PySide6 (Qt for Python)
- PDF: PyMuPDF + PyMuPDF4LLM
- AI: Anthropic Claude API
- Web自动化: Playwright + playwright_stealth
- 数据存储: SQLite
