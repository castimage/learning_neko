# 资料抽取：把 pdf / word / epub / 纯文本统一转成正文，供起始页提交
from __future__ import annotations

import posixpath
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

# 可直接按文本读取的后缀
TEXT_SUFFIXES = frozenset({'.txt', '.md', '.markdown', '.text', '.rst', '.csv', '.log'})
# 需要解析后取文本的后缀
DOCUMENT_SUFFIXES = frozenset({'.pdf', '.docx', '.epub'})
# 起始页接受的全部后缀
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | DOCUMENT_SUFFIXES

# ooxml 与 epub 里用到的命名空间
WORD_NAMESPACE = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
CONTAINER_NAMESPACE = '{urn:oasis:names:tc:opendocument:xmlns:container}'
OPF_NAMESPACE = '{http://www.idpf.org/2007/opf}'


# 抽取失败时抛出，消息直接面向用户
class DocumentError(Exception):
    pass


# 按后缀把文件抽取成正文
def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path)
    if suffix == '.pdf':
        return _read_pdf(path)
    if suffix == '.docx':
        return _read_docx(path)
    if suffix == '.epub':
        return _read_epub(path)
    raise DocumentError(f'暂不支持 {suffix or "该"} 格式的资料文件')


# 用常见中文编码依次尝试读取文本文件
def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode('utf-8', errors='replace')


# 用 pypdf 逐页抽取 PDF 文字
def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentError('未安装 pypdf，暂时读不了 PDF。请先运行：uv pip install pypdf') from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:                       # pypdf 的解析异常类型很杂，统一兜住
        raise DocumentError(f'无法解析 PDF：{exc}') from exc

    if reader.is_encrypted:
        try:
            reader.decrypt('')                      # 空密码能开的加密 PDF 直接解
        except Exception as exc:
            raise DocumentError('PDF 已加密，无法读取文字') from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception:
            pages.append('')                        # 单页坏了不至于整份失败
    return '\n'.join(pages)


# docx 本质是 zip，正文在 word/document.xml，按段落拼出文字
def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            payload = archive.read('word/document.xml')
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise DocumentError('不是有效的 Word(.docx) 文档') from exc

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise DocumentError(f'Word 正文解析失败：{exc}') from exc

    paragraphs = [
        ''.join(node.text or '' for node in para.iter(f'{WORD_NAMESPACE}t'))
        for para in root.iter(f'{WORD_NAMESPACE}p')
    ]
    return '\n'.join(paragraphs)


# epub 本质是 zip，按 spine 顺序读各章 xhtml 再抽文字
def _read_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            opf_path = _epub_opf_path(archive)
            opf_dir = opf_path.rsplit('/', 1)[0] if '/' in opf_path else ''
            opf = ElementTree.fromstring(archive.read(opf_path))

            chapters: list[str] = []
            for href in _epub_spine_hrefs(opf):
                member = posixpath.normpath(posixpath.join(opf_dir, href))
                try:
                    raw = archive.read(member)
                except KeyError:
                    continue
                chapters.append(_html_to_text(raw.decode('utf-8', errors='replace')))
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise DocumentError('不是有效的 EPUB 文件') from exc

    return '\n\n'.join(chapter for chapter in chapters if chapter.strip())


# 从 META-INF/container.xml 找到 opf 根文件路径
def _epub_opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = ElementTree.fromstring(archive.read('META-INF/container.xml'))
    except ElementTree.ParseError as exc:
        raise DocumentError('EPUB 根文件声明解析失败') from exc

    rootfile = container.find(f'{CONTAINER_NAMESPACE}rootfiles/{CONTAINER_NAMESPACE}rootfile')
    if rootfile is None or not rootfile.get('full-path'):
        raise DocumentError('EPUB 缺少根文件声明')
    return rootfile.get('full-path', '')


# 按 manifest 与 spine 解析出正文章节顺序
def _epub_spine_hrefs(opf: ElementTree.Element) -> list[str]:
    manifest = {
        item.get('id', ''): item.get('href', '')
        for item in opf.iter(f'{OPF_NAMESPACE}item')
        if item.get('id') and item.get('href')
    }

    hrefs: list[str] = []
    for itemref in opf.iter(f'{OPF_NAMESPACE}itemref'):
        href = manifest.get(itemref.get('idref', ''))
        if href:
            hrefs.append(href.split('#', 1)[0])
    return hrefs


# 把 xhtml 片段抽成纯文本，块级标签换行
class _HtmlTextExtractor(HTMLParser):
    _BLOCK_TAGS = frozenset({
        'p', 'div', 'br', 'li', 'tr', 'td', 'th',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'section', 'article', 'header', 'footer', 'blockquote', 'pre',
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ('script', 'style'):
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in ('script', 'style') and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    # 去掉空行与行首尾空白
    def text(self) -> str:
        lines = (line.strip() for line in ''.join(self._chunks).splitlines())
        return '\n'.join(line for line in lines if line)


# 把一段 html/xhtml 抽成纯文本
def _html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()
