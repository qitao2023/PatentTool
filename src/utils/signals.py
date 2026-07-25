"""
Qt 信号定义 - 用于 UI 线程与后台 Worker 线程之间的通信
"""
from PySide6.QtCore import QObject, Signal


class WorkerSignals(QObject):
    """Worker 线程向 UI 线程发送的信号集合"""
    progress = Signal(int, str)           # (百分比, 阶段描述)
    log = Signal(str, str)                # (级别: INFO/WARN/ERROR, 消息)
    pdf_done = Signal(object)             # PDF解析完成 -> PatentDocument
    queries_done = Signal(list)           # 检索式生成完成 -> list[SearchQuery]
    login_done = Signal(bool, str)        # (是否成功, 消息)
    query_complete = Signal(int, int, list)  # (查询序号, 总数, 结果列表)
    all_searches_done = Signal(list)      # 所有检索完成 -> list[list[PatentResult]]
    dedup_done = Signal(list, int)        # (去重后列表, 移除数量)
    fetch_done = Signal(list)             # 专利详情抓取完成 -> list[dict]
    analysis_done = Signal(object)        # 分析完成 -> AnalysisReport
    finished = Signal(bool, str)          # (成功/失败, 消息)
    error = Signal(str)                   # 错误消息
    captcha_required = Signal()           # 遇到验证码，需要用户干预
    browser_closed = Signal()             # 浏览器窗口被关闭
