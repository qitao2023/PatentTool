"""
报告生成模块 - 生成专利对比分析报告（HTML/JSON）
"""
import json
from datetime import datetime


class AnalysisReport:
    """分析报告对象，支持HTML/JSON多种格式输出"""

    def __init__(self, patent_doc=None, comparisons=None, dedup_results=None):
        self.patent_doc = patent_doc
        self.comparisons = comparisons or []
        self.dedup_results = dedup_results or []
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.markdown_content = ""
        self.html_content = ""

    def generate(self):
        """生成报告内容（Markdown + HTML）"""
        self.markdown_content = self._build_markdown()
        self.html_content = self._convert_md_to_html()

    def _build_markdown(self) -> str:
        """构建Markdown格式的报告"""
        lines = []
        lines.append("# 专利对比分析报告\n")
        lines.append(f"**生成时间**: {self.generated_at}\n")
        lines.append("---\n")

        # 1. 本申请概览
        if self.patent_doc:
            lines.append("## 一、本申请概览\n")
            lines.append(f"- **发明名称**: {self.patent_doc.title}")
            lines.append(f"- **公布号**: {self.patent_doc.publication_number}")
            lines.append(f"- **申请人**: {', '.join(self.patent_doc.applicants)}")
            lines.append(f"- **发明人**: {', '.join(self.patent_doc.inventors)}")
            lines.append(f"- **IPC分类**: {', '.join(self.patent_doc.ipc_classifications)}")
            lines.append("")
            lines.append("**摘要**:")
            lines.append(f"> {self.patent_doc.abstract}\n")
            lines.append("**权利要求**:\n")
            for i, claim in enumerate(self.patent_doc.claims[:10], 1):
                lines.append(f"{i}. {claim}")
            if len(self.patent_doc.claims) > 10:
                lines.append(f"\n*...共 {len(self.patent_doc.claims)} 项权利要求*")
            lines.append("")

        # 2. 检索概况
        lines.append("## 二、检索概况\n")
        lines.append(f"- **检索结果去重后**: {len(self.dedup_results)} 篇对比文献")
        lines.append(f"- **详细分析**: {len(self.comparisons)} 篇高相关度文献")
        lines.append("")

        # 3. 对比分析详情
        if self.comparisons:
            lines.append("## 三、对比分析详情\n")
            for i, comp in enumerate(self.comparisons, 1):
                pn = comp.get("publication_number", f"#{i}")
                score = comp.get("relevance_score", 0)
                lines.append(f"### {i}. {pn} （相关度: {score}/100）\n")

                lines.append(f"| 维度 | 评估 |")
                lines.append(f"|------|------|")
                lines.append(f"| 新颖性影响 | {comp.get('novelty_impact', 'N/A')} |")
                lines.append(f"| 创造性影响 | {comp.get('inventive_step_impact', 'N/A')} |")
                lines.append("")

                same = comp.get("key_features_same", [])
                if same:
                    lines.append("**相同技术特征**:")
                    for f in same:
                        lines.append(f"- {f}")
                    lines.append("")

                diff = comp.get("key_features_different", [])
                if diff:
                    lines.append("**不同技术特征**:")
                    for f in diff:
                        lines.append(f"- {f}")
                    lines.append("")

                conclusion = comp.get("conclusion", "")
                if conclusion:
                    lines.append(f"**综合评述**: {conclusion}\n")

                lines.append("---\n")

        # 4. 完整结果列表
        if self.dedup_results:
            lines.append("## 四、全部对比文献列表\n")
            lines.append("| 序号 | 公布号 | 标题 | 申请人 | 公开日 | 相关度 |")
            lines.append("|------|--------|------|--------|--------|--------|")
            for i, r in enumerate(self.dedup_results[:50], 1):
                pn = r.get("publication_number", "")
                title = r.get("title", "")[:40]
                applicant = r.get("applicant", "")[:20]
                pd = r.get("publication_date", "")
                score = r.get("relevance_score", "")
                score_str = f"{score}/100" if score else "-"
                lines.append(f"| {i} | {pn} | {title} | {applicant} | {pd} | {score_str} |")
            if len(self.dedup_results) > 50:
                lines.append(f"\n*...共 {len(self.dedup_results)} 条，仅显示前50条*")
            lines.append("")

        return "\n".join(lines)

    def _convert_md_to_html(self) -> str:
        """将Markdown转为HTML"""
        import markdown as md_lib
        html_body = md_lib.markdown(
            self.markdown_content,
            extensions=["tables", "fenced_code", "codehilite", "nl2br"]
        )

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>专利对比分析报告</title>
<style>
body {{ font-family: "Microsoft YaHei", "Segoe UI", sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px 30px; line-height: 1.8; color: #24292f; }}
h1 {{ color: #0969da; border-bottom: 2px solid #0969da; padding-bottom: 10px; }}
h2 {{ color: #0550ae; margin-top: 30px; border-bottom: 1px solid #d0d7de; padding-bottom: 6px; }}
h3 {{ color: #0550ae; margin-top: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #d0d7de; padding: 8px 12px; text-align: left; }}
th {{ background-color: #f6f8fa; font-weight: 600; }}
tr:nth-child(even) {{ background-color: #f8f9fa; }}
blockquote {{ border-left: 4px solid #0969da; margin: 12px 0; padding: 8px 16px; background: #f6f8fa; }}
code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
hr {{ border: none; border-top: 1px solid #d0d7de; margin: 24px 0; }}
.footer {{ text-align: center; color: #8b949e; font-size: 0.85em; margin-top: 40px; padding-top: 20px; border-top: 1px solid #d0d7de; }}
</style>
</head>
<body>
{html_body}
<div class="footer">
<p>专利检索分析工具 v1.0 | 生成时间: {self.generated_at}</p>
</div>
</body>
</html>"""

    def to_json(self) -> str:
        """导出为JSON"""
        return json.dumps({
            "generated_at": self.generated_at,
            "patent": {
                "title": self.patent_doc.title if self.patent_doc else "",
                "publication_number": self.patent_doc.publication_number if self.patent_doc else "",
                "abstract": self.patent_doc.abstract if self.patent_doc else "",
                "claims": self.patent_doc.claims if self.patent_doc else [],
                "ipc": self.patent_doc.ipc_classifications if self.patent_doc else [],
            } if self.patent_doc else {},
            "total_results": len(self.dedup_results),
            "detailed_comparisons": self.comparisons,
            "all_results": [
                {
                    "publication_number": r.get("publication_number", ""),
                    "title": r.get("title", ""),
                    "relevance_score": r.get("relevance_score", ""),
                }
                for r in self.dedup_results
            ],
        }, ensure_ascii=False, indent=2)

    def save(self, output_dir: str, formats: list[str] = None):
        """保存报告到文件"""
        from pathlib import Path
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        if formats is None:
            formats = ["json", "html"]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"patent_analysis_{timestamp}"

        if "html" in formats:
            (out / f"{base_name}.html").write_text(self.html_content, encoding="utf-8")

        if "json" in formats:
            (out / f"{base_name}.json").write_text(self.to_json(), encoding="utf-8")

        if "md" in formats:
            (out / f"{base_name}.md").write_text(self.markdown_content, encoding="utf-8")

        return str(out / base_name)
