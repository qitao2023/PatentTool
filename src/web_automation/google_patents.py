"""
Google Patents HTML 提取模块 — 免登录、纯 HTTP 获取专利全文。

链路（已实测验证，2026-08-01）：
    urllib GET https://patents.google.com/patent/{公开号}/zh
        ↓  HTTP 200，原始 HTML 含完整中文全文
    lxml 解析分节:
        div.abstract / section.abstract      → 摘要
        div.description-paragraph / section.description → 说明书
        div.claim / div.claim-dependent / section.claims → 权利要求

关键实测事实（勿改）：
- Google Patents 响应 Content-Type 不带 charset，必须显式 utf-8 解码再解析，
  否则 lxml 猜错编码出乱码
- `/zh` 后缀页：仅 CN 专利（原文即中文）服务端渲染全文；非 CN 专利
  （US/WO/JP等）的 `/zh`、`/en` 翻译页服务端**不渲染**全文（靠 JS 懒加载）
- **不带后缀页**（/patent/{pub}）：服务端渲染**原语言**全文，所有专利都有
- 同一 URL 不同请求可能返回不同模板（A/B 或软限流），需多尝试 + 重试
- PATENTSCOPE 的公开号显示格式 `WO/2012/014775` 需去掉斜杠 → `WO2012014775`

输出 dict 兼容 PATENTSCOPE 的 _extract_detail_page 格式，可直接写 JSON 给
下游 AI 评分流程消费。书目字段（applicant/inventor/ipc/日期）优先从
搜索结果元数据继承（调用方传入 search_meta），Google 仅提供 meta 标签兜底。
"""
from __future__ import annotations

import http.client
import re
import time
import urllib.request
import urllib.error
from typing import Optional

from lxml import html as lxml_html

from src.utils.patent_extract import extract_embodiments


GOOGLE_PATENTS_BASE = "https://patents.google.com/patent/{pub}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 最近一次 fetch_patent_text 失败原因（诊断日志用）。
# 高并发下多线程写读仅作参考；三轮串行终检时精确反映该篇失败原因。
LAST_FETCH_ERROR = ""

# 尝试的 URL 变体顺序：/zh（中文，CN 原文）→ 无后缀（原语言全文）
_URL_VARIANTS = ["/zh", ""]


def _normalize_pub(pub: str) -> str:
    """公开号去空格/斜杠、转大写。

    PATENTSCOPE 显示格式 `WO/2012/014775` → `WO2012014775`。
    """
    return re.sub(r"[\s/]+", "", (pub or "").strip()).upper()


def _page_urls(pub: str) -> list[str]:
    """候选 URL：/zh 优先（中文原文），无后缀兜底（原语言全文）。"""
    return [GOOGLE_PATENTS_BASE.format(pub=pub) + v
            for v in _URL_VARIANTS]


def _http_get(url: str, proxy: str | None, timeout: int) -> bytes:
    """GET 页面，返回原始字节。走代理则用 ProxyHandler。"""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _clean_text(s: str) -> str:
    """压缩空白，保留换行语义。"""
    s = re.sub(r"[ \t\r]+", " ", s or "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _try_fetch_tree(url: str, proxy: str | None,
                    timeout: int, attempts: int = 3) -> object | None:
    """抓取并解析页面，带重试。返回 lxml 树或 None。

    ⚠️ http.client.HTTPException（IncompleteRead / RemoteDisconnected 等）
    必须纳入重试：高并发下代理会中途截断大响应，属瞬时传输错误，单次即
    放弃会被误判为"无全文"。重试耗尽后在 LAST_FETCH_ERROR 记录真实原因。
    """
    global LAST_FETCH_ERROR
    last_err = None
    for i in range(attempts):
        try:
            html_bytes = _http_get(url, proxy, timeout)
            # ⚠️ 必须显式 UTF-8 解码：Google 响应无 charset，直接
            # fromstring(bytes) 会猜错编码产生乱码。
            return lxml_html.fromstring(html_bytes.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, TimeoutError, UnicodeDecodeError,
                http.client.HTTPException) as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(1.0 + i)
    if last_err is not None:
        LAST_FETCH_ERROR = (f"网络/代理错误({type(last_err).__name__}): "
                            f"{last_err}")
    return None


def _extract_meta(tree, name: str) -> list[str]:
    """提取指定 name 的 meta 标签 content。"""
    out = []
    for m in tree.xpath(f"//meta[@name='{name}']"):
        c = (m.get("content") or "").strip()
        if c:
            out.append(c)
    return out


def _get_section(tree, heading: str):
    """按 section 开头标题识别区块（跨模板，不依赖 class）。

    Google Patents 同一 URL 不同请求会返回带 class 与不带 class 两种模板，
    靠 class 选 section 不可靠，必须按标题文本识别。
    """
    for sec in tree.xpath("//section"):
        t = (sec.text_content() or "").lstrip()
        if re.match(rf"^{heading}\b", t, re.IGNORECASE):
            return sec
    return None


def _extract_abstract(tree) -> str:
    """摘要：div.abstract 优先，无则按标题识别 section。"""
    for el in tree.xpath("//div[@class='abstract']"):
        t = _clean_text(el.text_content())
        if t:
            return t
    sec = _get_section(tree, "Abstract")
    if sec is not None:
        return _clean_text(sec.text_content())
    return ""


def _extract_description(tree) -> str:
    """说明书：description-paragraph 逐段优先，无则按标题识别 section。"""
    paras = []
    for p in tree.xpath("//div[@class='description-paragraph']"):
        t = _clean_text(p.text_content())
        if t:
            paras.append(t)
    if paras:
        return "\n\n".join(paras)
    sec = _get_section(tree, "Description")
    if sec is not None:
        t = _clean_text(sec.text_content())
        # 去掉 section 自带的 "Description" 标题
        t = re.sub(r"^Description\b\s*", "", t)
        return t
    return ""


def _extract_claims(tree) -> str:
    """权利要求：.claims 容器直接子节点优先，无则按标题识别 section。

    ⚠️ 不能写 `//div[@class='claim']`：claim div 内部还嵌套相同 class 的
    子节点会重复，必须限定 `.claims` 容器的直接子节点。
    """
    seen = set()
    claims = []
    for c in tree.xpath(
            "//div[@class='claims']/div[@class='claim' or @class='claim-dependent']"):
        t = _clean_text(c.text_content())
        if len(t) < 10 and "Figure" in t:
            continue
        if t and t not in seen:
            seen.add(t)
            claims.append(t)
    if claims:
        return "\n".join(claims)
    sec = _get_section(tree, "Claims")
    if sec is not None:
        t = _clean_text(sec.text_content())
        t = re.sub(r"^Claims?\s*\(\d+\)\s*", "", t)
        return t
    return ""


def _extract_ipc(tree) -> str:
    """IPC/CPC 分类号：span[@itemprop='Code']，去重取前 3 个。"""
    codes = []
    for el in tree.xpath("//span[@itemprop='Code']"):
        t = el.text_content().strip()
        if t and "/" in t and t not in codes:
            codes.append(t)
    return "; ".join(codes[:3])


def _extract_title(tree) -> str:
    """标题：优先 meta DC.title，其次 <title>。"""
    titles = _extract_meta(tree, "DC.title")
    if titles:
        return _clean_text(titles[0])
    t = tree.xpath("string(//title)")
    t = re.sub(r"\s*-\s*Google\s*Patents\s*$", "", t).strip()
    return t.strip()


def _detect_lang(text: str) -> str:
    """粗略判断文本语言：中文 zh / 日文 ja / 韩文 ko / 拉丁 en。

    假名（぀-ヿ）和谚文（가-힯）是日/韩独有的，出现即判该语言。
    """
    if not text:
        return "other"
    han = len(re.findall(r"[一-鿿]", text))          # 汉字
    kana = len(re.findall(r"[぀-ヿ]", text))          # 日文假名
    hangul = len(re.findall(r"[가-힯]", text))        # 韩文
    latin = len(re.findall(r"[A-Za-z]", text))
    total = han + kana + hangul + latin
    if total == 0:
        return "other"
    if kana > 0:
        return "ja"
    if hangul > 0:
        return "ko"
    if (han + kana + hangul) >= latin:
        return "zh"
    return "en"


# ── Google Patents 搜索（Playwright，结果页 JS 渲染）──────────────

# 单页上限（实测 num=100 为最大，无程序化分页）
GOOGLE_SEARCH_PAGE_MAX = 100

# 提取每个 search-result-item 的标题/公开号/申请人/日期/摘要。
# Google 用 Polymer shadow DOM，需递归穿透；申请人在 shadow 深层懒加载，
# 改用 item.innerText（浏览器会扁平化所有 shadow 文本）做文本解析。
_GOOGLE_SEARCH_EXTRACT_JS = """(maxItems) => {
    function deepQ(root, sel) {
        const f = root.querySelector(sel);
        if (f) return f;
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) {
                const g = deepQ(el.shadowRoot, sel);
                if (g) return g;
            }
        }
        return null;
    }
    function deepQAll(root, sel) {
        const out = [];
        root.querySelectorAll(sel).forEach(e => out.push(e));
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) deepQAll(el.shadowRoot, sel).forEach(e => out.push(e));
        }
        return out;
    }
    const results = [];
    let declared = null;
    // Google 页头估算总数（"About 18 results" / "More than 100,000 results"），
    // 用于与实际渲染数对比日志。数字带千分位逗号（100,000），必须去逗号再转 int，
    // 否则正则会匹配到 ",000" 尾巴算出 0 条（误导成"声明0条但渲染N条"）。
    const declaredM = (document.body.innerText || '').match(/(?:About|More than)?\\s*([\\d,]+)\\s*results?/i);
    if (declaredM) declared = parseInt(declaredM[1].replace(/,/g, ''), 10);
    document.querySelectorAll('search-result-item').forEach(item => {
        const root = item.shadowRoot || item;
        const title = (deepQ(root, 'h3')?.textContent || '').trim();
        // 公开号：PDF 链接文字优先（如 US11996334B2）
        const pubs = [];
        deepQAll(root, 'a[href*="patentimages"]').forEach(a => {
            const t = a.textContent.trim();
            if (t && /[A-Z]{2,3}\\d/.test(t) && !pubs.includes(t)) pubs.push(t);
        });
        // 文本解析（innerText 扁平化所有 shadow）
        const text = (item.innerText || item.textContent || '').replace(/\\u00a0/g, ' ');
        const lines = text.split('\\n').map(l => l.replace(/[ \\t]+/g, ' ').trim()).filter(Boolean);
        // ⚠️ 部分专利（老日文/EP 等）搜索项无 PDF 链接 → 从文本正则兜底
        // 公开号 2-3 字母 + 数字：CN116110953A / US11996334B2 / TWI657579B /
        // JPH11261028A（老日本 JP+H 平成前缀，3 字母）；扫描所有行
        if (!pubs.length) {
            for (const line of lines) {
                const m = line.match(/\\b[A-Z]{2,3}\\d{4,}[A-Z]?\\d*\\b/);
                if (m) { pubs.push(m[0]); break; }
            }
        }
        let pubDate = '', abstract = '';
        for (let i = 0; i < lines.length; i++) {
            const m = lines[i].match(/Published\\s+(\\d{4}-\\d{2}-\\d{2})/);
            if (m) { pubDate = m[1]; abstract = lines.slice(i + 1).join(' ').trim(); break; }
        }
        // 申请人：第2行去掉国家码和公开号后的剩余文本
        let assignee = '';
        if (lines.length > 1) {
            assignee = lines[1]
                .replace(/\\b(US|CN|KR|DE|TW|EP|JP|WO|FR|GB|IN)\\b/g, ' ')
                .replace(/\\b[A-Z]{2,3}\\d{4,}[A-Z]?\\d*\\b/g, ' ')
                .replace(/\\s+/g, ' ').trim();
        }
        if (title && pubs.length) {
            results.push({title, pub: pubs[0], abstract, assignee, pubDate});
        }
        if (results.length >= maxItems) return {items: results, declared};
    });
    return {items: results, declared};
}"""

# 统计已渲染的完整结果数（含标题的 search-result-item），排除骨架节点
_GOOGLE_COUNT_JS = """() => {
    let n = 0;
    for (const it of document.querySelectorAll('search-result-item')) {
        if ((it.shadowRoot || it).querySelector('h3')) n++;
    }
    return n;
}"""


def _translate_query(query: str) -> str:
    """PATENTSCOPE 检索式 → Google 可理解的检索式。

    - IC:(H01L-29) → ipc:H01L29（Google 支持 ipc: 前缀；裸 token 不检索分类）
    - 其他字段前缀（FP:/EN_AB:/AB: 等）剥离保留括号内容与布尔逻辑
    - 实测：`ipc:H01L29` 返回结果，`H01L29` 裸 token 返回 0 条
    """
    def _ic_repl(m):
        code = m.group(2).replace("-", "").replace(" ", "").replace(" ", "")
        return f"ipc:{code}"
    q = re.sub(r"\b(IC|IPC)\s*:\s*\(([^()]*)\)", _ic_repl, query)
    q = re.sub(r"\b(FP|EN_AB|AB|TAC|APN|AN|PA|IN|TI)\s*:\s*\(([^()]*)\)",
               r"\2", q)
    q = re.sub(r"\b(FP|IC|IPC|EN_AB|AB|TAC|APN|AN|PA|IN|TI)\s*:", "", q)
    return q.strip()


async def search_abstracts(page, query: str, max_results: int = 100,
                           signals=None) -> list[dict]:
    """Google Patents 搜索，返回与 PATENTSCOPE search_abstracts 兼容的 dict 列表。

    Args:
        page: Playwright Page（需走代理已配置）
        query: 检索式（支持 PATENTSCOPE 前缀，会被剥离）
        max_results: 上限（Google 单页上限 100，超过自动截断）
        signals: WorkerSignals 兼容对象

    Returns:
        list[dict]: 含 doc_id/publication_number/title/abstract_snippet 等
    """
    from urllib.parse import quote
    num = min(max_results, GOOGLE_SEARCH_PAGE_MAX)
    url = f"https://patents.google.com/?q={quote(query)}&num={num}"

    if signals:
        signals.log.emit("INFO", f"  [Google] 搜索: {query}")
        if max_results > GOOGLE_SEARCH_PAGE_MAX:
            signals.log.emit("WARN",
                f"  Google 单页上限 {GOOGLE_SEARCH_PAGE_MAX} 条，"
                f"本检索式将截断（请求 {max_results}）")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    # 轮询等待结果稳定渲染：首个 h3 出现后，数量连续两次不再增长才认为渲染完成。
    # Google 会分批/懒加载结果，过早提取会漏掉后渲染的条目。
    prev = -1
    stable = 0
    for _ in range(15):
        n = await page.evaluate(_GOOGLE_COUNT_JS)
        if n > 0 and n == prev:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prev = n
        await page.wait_for_timeout(1500)
    # 稍等 shadow DOM 内容渲染完成再提取
    await page.wait_for_timeout(800)

    data = await page.evaluate(_GOOGLE_SEARCH_EXTRACT_JS, max_results)
    items = data["items"]
    declared = data.get("declared")
    results = []
    for it in items:
        pub = it["pub"]
        results.append({
            "doc_id": pub,
            "publication_number": pub,
            "patent_number": pub,
            "title": it["title"],
            "abstract_snippet": it["abstract"][:500],
            "applicant": it["assignee"],
            "inventor": "",
            "ipc": "",
            "publication_date": it["pubDate"],
            "source_query": query,
        })
    if signals:
        msg = f"  [Google] 搜索完成: {len(results)} 篇"
        if declared is not None and declared != len(results):
            # Google 页头 "About N results" 是估算数（含同族/未去重），
            # 实际渲染数才是本页可取到的结果，如实说明避免误判漏抓
            msg += f"（Google 声明约{declared}条，实际渲染{len(results)}条）"
        signals.log.emit("SUCCESS", msg)
    return results


async def search_abstracts_parallel(
    page, queries: list[str], max_results: int = 100,
    signals=None, concurrency: int = 3,
) -> list[dict]:
    """并行执行多个 Google 检索式搜索。

    在同一浏览器 context 下额外开 (concurrency-1) 个标签页并行搜索，
    每个标签页串行处理分到的检索式（轮询分配，均衡负载）。返回与
    queries 顺序一致的结果列表，供调用方决定哪些需要重试。

    Args:
        page: Playwright Page（工作标签页之一，其余从 page.context 新建）
        queries: 检索式字符串列表
        max_results: 每式上限
        signals: WorkerSignals 兼容对象
        concurrency: 并行标签页数（<=1 或单检索式时自动退化为串行）

    Returns:
        list[dict]: 与 queries 等长，每项
            {"abstracts": list[dict], "error": str|None}
    """
    import asyncio

    queries = [str(q).strip() for q in queries]
    n = len(queries)
    if n == 0:
        return []
    # 全部为空检索式 → 直接返回空结果，不动浏览器
    if not any(queries):
        return [{"abstracts": [], "error": None} for _ in queries]

    # ── 串行退化路径（单检索式 / 并发<=1）──
    if concurrency <= 1 or n == 1:
        out = []
        for q in queries:
            if not q:
                out.append({"abstracts": [], "error": None})
                continue
            try:
                abstracts = await search_abstracts(
                    page, q, max_results=max_results, signals=signals)
                out.append({"abstracts": abstracts, "error": None})
            except Exception as e:
                out.append({"abstracts": [], "error": str(e)})
        return out

    context = page.context
    created = []
    try:
        # 复用同一 context 开额外标签页（代理/会话一致）
        for _ in range(min(concurrency, n) - 1):
            p = await context.new_page()
            p.set_default_timeout(60000)
            created.append(p)
        pool = [page] + created
        results: list = [None] * n

        async def _worker(pg, idxs):
            for idx in idxs:
                q = queries[idx]
                if not q:
                    results[idx] = {"abstracts": [], "error": None}
                    continue
                try:
                    abstracts = await search_abstracts(
                        pg, q, max_results=max_results, signals=signals)
                    results[idx] = {"abstracts": abstracts, "error": None}
                except Exception as e:
                    results[idx] = {"abstracts": [], "error": str(e)}

        # 轮询分配下标，均衡各标签页工作量
        n_pages = len(pool)
        tasks = [
            asyncio.create_task(_worker(pool[i], range(i, n, n_pages)))
            for i in range(n_pages)
        ]
        await asyncio.gather(*tasks)
        return results
    finally:
        # 关闭额外标签页，还原为单页状态（page 由调用方持有/关闭）
        for p in created:
            try:
                await p.close()
            except Exception:
                pass


def fetch_patent_text(pub: str, proxy: str | None = None,
                      timeout: int = 20,
                      search_meta: Optional[dict] = None) -> Optional[dict]:
    """获取一篇专利的全文文本。

    策略（实测验证）：
      1. 先试 /zh 页 → CN 专利（原文中文）服务端渲染全文
      2. /zh 无全文（非 CN 专利翻译页不渲染）→ 试无后缀页 → 原语言全文
      3. 两个变体都失败 → None（调用方降级 PATENTSCOPE）

    Args:
        pub: 公开号，如 CN116110953A / WO2012014775 / WO/2012/014775
        proxy: http 代理，如 http://127.0.0.1:7892；None 则不代理
        timeout: 请求超时（秒）
        search_meta: 搜索阶段结果 dict，用于继承 applicant/inventor/ipc 等

    Returns:
        dict 兼容 PATENTSCOPE 提取格式；失败返回 None。
    """
    global LAST_FETCH_ERROR
    LAST_FETCH_ERROR = ""
    pub = _normalize_pub(pub)
    if not pub:
        LAST_FETCH_ERROR = "无效公开号"
        return None

    for url in _page_urls(pub):
        tree = _try_fetch_tree(url, proxy, timeout)
        if tree is None:
            continue
        abstract = _extract_abstract(tree)
        claims = _extract_claims(tree)
        description = _extract_description(tree)
        title = _extract_title(tree)
        # 有效：claims 或 description 任一非空即可（非 CN 原语言也接受）
        if not (claims or description):
            LAST_FETCH_ERROR = "Google Patents 无全文"
            continue

        used_zh = url.endswith("/zh")
        lang = "zh" if used_zh else _detect_lang(claims or description)

        result: dict = {
            "publication_number": pub,
            "patent_number": pub,
            "title": title,
            "abstract": abstract[:5000],
            "claims": claims[:10000],
            "description": description[:20000],
            "embodiments": extract_embodiments(description),  # 具体实施方式（说明书最后大节）
            "ipc": "",
            "applicant": "",
            "inventor": "",
            "publication_date": "",
            "application_number": "",
            "fetch_status": "ok",
            "_source": "google_patents",
            "_lang": lang,
        }

        # ── 书目字段：搜索结果继承优先，Google meta 兜底 ──
        meta = search_meta or {}
        for f in ("applicant", "inventor", "ipc", "publication_date",
                  "application_number"):
            if meta.get(f):
                result[f] = meta[f]

        if not result["ipc"]:
            result["ipc"] = _extract_ipc(tree)
        if not result["inventor"]:
            invs = []
            for m in tree.xpath(
                    "//meta[@name='DC.contributor' and @scheme='inventor']"):
                c = (m.get("content") or "").strip()
                if c and c not in invs:
                    invs.append(c)
            result["inventor"] = "; ".join(invs)
        if not result["publication_date"]:
            dates = _extract_meta(tree, "DC.date")
            if dates:
                result["publication_date"] = dates[-1]
        if not result["application_number"]:
            apps = _extract_meta(tree, "citation_patent_application_number")
            if apps:
                result["application_number"] = apps[0].replace("CN:", "")

        if not result["abstract"] and meta.get("abstract_snippet"):
            result["abstract"] = meta["abstract_snippet"]

        LAST_FETCH_ERROR = ""  # 成功即无失败原因
        return result

    if not LAST_FETCH_ERROR:
        LAST_FETCH_ERROR = "未知原因"
    return None
