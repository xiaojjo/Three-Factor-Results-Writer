# -*- coding: utf-8 -*-
"""
md2docx.py — 零依赖 Markdown → Word(.docx) 转换器
====================================================================
用法：
    python md2docx.py 输入.md [输出.docx]

    不写输出路径时，自动生成与输入同名的 .docx（如 论文.md -> 论文.docx）。

设计目标——跨机器可移植：
    本脚本【仅依赖 Python 3 标准库】（zipfile + xml），不依赖 python-docx、
    markdown 等第三方包，也无需联网 pip 安装。因此只要目标机器上能找到
    任意一个 Python 3 解释器（系统 python / python3，或 WorkBuddy 托管 Python），
    即可直接运行，不受具体安装路径、虚拟环境、是否预装库的限制。

支持的 Markdown：
    标题(# ~ ######)、段落、**粗体**、*斜体*、`行内代码`、代码块(```)、
    有序/无序列表、表格(管道语法)、引用(>)、分隔线(---)。

固化的排版规范（沿用 typeset7.py，与期刊投稿格式一致）：
    正文   ：中文宋体 + 西文 Times New Roman，12pt，两端对齐，1.5 倍行距，
             段前段后 6pt。
    标题   ：中文黑体 + 西文 Times New Roman，加粗，
             H1=16pt H2=14pt H3=13pt H4~H6=12pt，左对齐。
    页面   ：A4，四边页边距 2.54cm。
    斜体   ：md 中的 *xxx* 保留为 Word 斜体（用于统计符号 F / P / χ² 等）。
    表格   ：细实线网格边框，表头加粗，单元格 10.5pt。
    代码块 ：Consolas 10.5pt，逐行保留。

依赖：无（仅 Python 3.8+ 标准库）。
====================================================================
"""
import sys
import io
import re
import zipfile

# ------------------------------------------------------------------ 常量
BODY_CN, BODY_EN = "SimSun", "Times New Roman"      # 正文中文字体 / 西文字体
HEAD_CN, HEAD_EN = "SimHei", "Times New Roman"      # 标题中文字体 / 西文字体
CODE_EN = "Consolas"
BODY_SIZE = 12
HEAD_SIZE = {1: 16, 2: 14, 3: 13, 4: 12, 5: 12, 6: 12}
MARGIN_TWIPS = 1440          # 2.54cm = 1440 twips (1 inch = 1440 twips)
A4_W, A4_H = 11906, 16838    # A4 in twips
LIST_INDENT = 420            # 0.74cm
LIST_HANG = -300             # 悬挂缩进
LINE_15 = 360                # 1.5 倍行距 = 240 * 1.5
SPACE_6 = 120               # 6pt


# ------------------------------------------------------------------ XML 工具
def esc(s):
    """转义 XML 文本中的保留字符。"""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def xml_decl():
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


# ------------------------------------------------------------------ 内联解析
# 匹配 **粗体** / *斜体* / `行内代码`
_INLINE_RE = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`')


def parse_inline(s):
    """把一段文本拆成 (text, bold, italic, code) 元组列表。"""
    runs = []
    last = 0
    for m in _INLINE_RE.finditer(s):
        if m.start() > last:
            runs.append((s[last:m.start()], False, False, False))
        if m.group(1) is not None:
            runs.append((m.group(1), True, False, False))
        elif m.group(2) is not None:
            runs.append((m.group(2), False, True, False))
        elif m.group(3) is not None:
            runs.append((m.group(3), False, False, True))
        last = m.end()
    if last < len(s):
        runs.append((s[last:], False, False, False))
    return runs


def run_xml(content, bold=False, italic=False, code=False,
            cn=BODY_CN, en=BODY_EN, sz=BODY_SIZE):
    """生成单个 <w:r> 片段。"""
    if code:
        en = CODE_EN
        sz = 10.5
    rpr = ['<w:rPr>']
    rpr.append(f'<w:rFonts w:ascii="{en}" w:hAnsi="{en}" w:eastAsia="{cn}" w:cs="{en}"/>')
    if bold:
        rpr.append('<w:b/><w:bCs/>')
    if italic:
        rpr.append('<w:i/><w:iCs/>')
    half = int(round(sz * 2))
    rpr.append(f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>')
    rpr.append('</w:rPr>')
    return f'<w:r>{"".join(rpr)}<w:t xml:space="preserve">{esc(content)}</w:t></w:r>'


def runs_for(text, base_bold=False, base_italic=False,
             cn=BODY_CN, en=BODY_EN, sz=BODY_SIZE):
    """把一段文本（可能含 **/*/` 标记）转成 runs 的 XML 拼接。"""
    out = []
    for (t, b, it, c) in parse_inline(text):
        if t == '':
            continue
        out.append(run_xml(t, bold=(base_bold or b), italic=(base_italic or it),
                           code=c, cn=cn, en=en, sz=sz))
    return ''.join(out)


def para_xml(runs_xml, align='both', sb=SPACE_6, sa=SPACE_6, line=LINE_15,
             indent=None, first_line=None):
    """生成 <w:p> 段落。"""
    ppr = ['<w:pPr>']
    ppr.append(f'<w:jc w:val="{align}"/>')
    if indent is not None:
        fl = first_line if first_line is not None else 0
        ppr.append(f'<w:ind w:left="{indent}" w:firstLine="{fl}"/>')
    ppr.append(f'<w:spacing w:before="{sb}" w:after="{sa}" '
               f'w:line="{line}" w:lineRule="auto"/>')
    ppr.append('</w:pPr>')
    return f'<w:p>{"".join(ppr)}{runs_xml}</w:p>'


# ------------------------------------------------------------------ 块级渲染
def render_heading(content, level):
    sz = HEAD_SIZE.get(level, 12)
    runs = runs_for(content, base_bold=True, cn=HEAD_CN, en=HEAD_EN, sz=sz)
    sb = 360 if level == 1 else 240
    return para_xml(runs, align='left', sb=sb, sa=SPACE_6, line=LINE_15)


def render_paragraph(content):
    runs = runs_for(content, base_bold=False)
    return para_xml(runs, align='both', sb=SPACE_6, sa=SPACE_6, line=LINE_15)


def render_quote(content):
    runs = runs_for(content, base_bold=False)
    return para_xml(runs, align='both', sb=SPACE_6, sa=SPACE_6, line=LINE_15,
                    indent=LIST_INDENT)


def render_hr():
    runs = run_xml('—' * 12)
    return para_xml(runs, align='center', sb=SPACE_6, sa=SPACE_6)


def render_code_block(code_text):
    segs = code_text.split('\n')
    runs_xml = ''
    for k, seg in enumerate(segs):
        if k > 0:
            runs_xml += '<w:br/>'
        runs_xml += run_xml(seg, code=True)
    return para_xml(runs_xml, align='left', sb=SPACE_6, sa=SPACE_6, line=240)


def render_list_item(content, ordered, counter):
    prefix = f'{counter}. ' if ordered else '• '
    runs = run_xml(prefix)
    runs += runs_for(content, base_bold=False)
    return para_xml(runs, align='both', sb=0, sa=60,
                    line=LINE_15, indent=LIST_INDENT, first_line=LIST_HANG)


def render_table(rows):
    """rows: list[list[str]]，首行为表头。"""
    if not rows:
        return ''
    ncols = max(len(r) for r in rows)
    borders = ('<w:tblBorders>'
               '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
               '</w:tblBorders>')
    tblpr = (f'<w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}'
             '<w:tblLook w:val="04A0"/></w:tblPr>')
    grid = '<w:tblGrid>' + ''.join('<w:gridCol w:w="3000"/>' for _ in range(ncols)) + '</w:tblGrid>'
    trs = []
    for ri, row in enumerate(rows):
        is_head = (ri == 0)
        cells = []
        for ci in range(ncols):
            cell_text = row[ci] if ci < len(row) else ''
            cell_runs = runs_for(cell_text, base_bold=is_head, sz=10.5)
            cell_p = (f'<w:p><w:pPr><w:spacing w:before="40" w:after="40" '
                      f'w:line="240" w:lineRule="auto"/></w:pPr>{cell_runs}</w:p>')
            tc = (f'<w:tc><w:tcPr><w:tcMar>'
                  f'<w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
                  f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>'
                  f'</w:tcMar></w:tcPr>{cell_p}</w:tc>')
            cells.append(tc)
        trs.append(f'<w:tr>{"".join(cells)}</w:tr>')
    return f'<w:tbl>{tblpr}{grid}{"".join(trs)}</w:tbl>'


# ------------------------------------------------------------------ 主解析
_LIST_RE = re.compile(r'^(\s*)([-*+]|\d+[\.\)])\s+(.*)$')
_SEP_RE = re.compile(r'^\s*\|?[\s:\-|]+\|?\s*$')


def classify(stripped):
    if stripped == '':
        return 'blank'
    if stripped.startswith('```'):
        return 'code'
    if stripped.startswith('#'):
        return 'heading'
    if stripped.startswith('>'):
        return 'quote'
    if stripped in ('---', '***', '___'):
        return 'hr'
    if _LIST_RE.match(stripped):
        return 'list'
    return 'para'


def convert(md_text):
    lines = md_text.split('\n')
    n = len(lines)
    blocks = []
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块
        if stripped.startswith('```'):
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结束 ```
            blocks.append(render_code_block('\n'.join(code_lines)))
            continue

        # 标题
        if stripped.startswith('#'):
            level = len(stripped) - len(stripped.lstrip('#'))
            level = max(1, min(6, level))
            content = stripped[level:].strip()
            blocks.append(render_heading(content, level))
            i += 1
            continue

        # 引用
        if stripped.startswith('>'):
            q_lines = []
            while i < n and lines[i].strip().startswith('>'):
                q_lines.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(render_quote(' '.join(q_lines)))
            continue

        # 分隔线
        if stripped in ('---', '***', '___'):
            blocks.append(render_hr())
            i += 1
            continue

        # 表格（当前行含 | 且下一行是分隔行）
        if '|' in stripped and i + 1 < n and '-' in lines[i + 1] \
                and _SEP_RE.match(lines[i + 1].strip()):
            header = [c.strip() for c in stripped.strip().strip('|').split('|')]
            i += 2
            rows = [header]
            while i < n and lines[i] is not None:
                s = lines[i].strip()
                if s == '' or '|' not in s:
                    break
                rows.append([c.strip() for c in s.strip().strip('|').split('|')])
                i += 1
            blocks.append(render_table(rows))
            continue

        # 列表
        if _LIST_RE.match(stripped):
            items = []
            while i < n:
                s = lines[i]
                st = s.strip()
                m = _LIST_RE.match(st)
                if m:
                    items.append((bool(re.match(r'\d', m.group(2))), m.group(3)))
                    i += 1
                elif st == '':
                    j = i + 1
                    if j < n and _LIST_RE.match(lines[j].strip()):
                        i += 1
                        continue
                    break
                else:
                    break
            counter = 1
            for (ordered, content) in items:
                num = counter
                if ordered:
                    counter += 1
                blocks.append(render_list_item(content, ordered, num))
            continue

        # 普通段落（聚合连续非空白、非特殊行）
        if stripped != '':
            para_parts = []
            while i < n:
                st = lines[i].strip()
                if st == '' or classify(st) != 'para':
                    break
                para_parts.append(st)
                i += 1
            blocks.append(render_paragraph(' '.join(para_parts)))
            continue

        # 空白行
        i += 1

    # 组装 document.xml
    sect = ('<w:sectPr>'
            f'<w:pgSz w:w="{A4_W}" w:h="{A4_H}"/>'
            f'<w:pgMar w:top="{MARGIN_TWIPS}" w:right="{MARGIN_TWIPS}" '
            f'w:bottom="{MARGIN_TWIPS}" w:left="{MARGIN_TWIPS}" '
            'w:header="720" w:footer="720" w:gutter="0"/>'
            '</w:sectPr>')
    body = f'<w:body>{"".join(blocks)}{sect}</w:body>'
    return (xml_decl() +
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'{body}</w:document>')


# ------------------------------------------------------------------ 打包 docx
def build_docx(document_xml, dst):
    content_types = (xml_decl() +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (xml_decl() +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>')
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', document_xml)


# ------------------------------------------------------------------ 入口
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    # utf-8-sig 自动剥离 BOM，避免开头 # 标题被破坏
    with io.open(src, encoding='utf-8-sig') as f:
        md_text = f.read()
    if len(sys.argv) >= 3:
        dst = sys.argv[2]
    else:
        dst = re.sub(r'\.md$', '.docx', src, flags=re.I)
    document_xml = convert(md_text)
    build_docx(document_xml, dst)
    print(f"✓ 已转换：{src} -> {dst}")


if __name__ == '__main__':
    main()
