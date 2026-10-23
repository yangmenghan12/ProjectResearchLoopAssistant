from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT_NAME = "STSong-Light"
PAGE_MARGIN = 48
TITLE_SIZE = 16
BODY_SIZE = 10
TITLE_LEADING = 24
BODY_LEADING = 14


def render_markdown_pdf(title: str, markdown: str) -> bytes:
    """将 Markdown 的逐行文本渲染为支持中文字体的 PDF 字节流。

    Args: title 为 PDF 标题；markdown 为待渲染正文字符串。
    Returns: 可直接作为 HTTP body 的 PDF bytes。
    Raises: ReportLab 字体注册或渲染失败时抛出异常。
    副作用：仅在内存中生成 PDF；由报告导出 handler 调用。
    """
    buffer = BytesIO()
    _, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(title)

    # ReportLab 的 CID 字体可直接支持中文报告内容，避免默认字体遇到中文编码报错。
    register_chinese_font()

    y = page_height - PAGE_MARGIN

    def draw_line(text: str, font_size: int, leading: int) -> None:
        nonlocal y
        if y < PAGE_MARGIN:
            pdf.showPage()
            y = page_height - PAGE_MARGIN
        pdf.setFont(FONT_NAME, font_size)
        pdf.drawString(PAGE_MARGIN, y, text)
        y -= leading

    draw_line(title, TITLE_SIZE, TITLE_LEADING)
    for line in markdown.splitlines():
        draw_line(line, BODY_SIZE, BODY_LEADING)

    pdf.save()
    return buffer.getvalue()


def register_chinese_font() -> None:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))
