# -*- coding: utf-8 -*-
"""
md2docx.py — Zero-dependency Markdown → Word(.docx) converter
====================================================================
Usage:
    python md2docx.py input.md [output.docx]

    If no output path is given, a .docx file with the same base name will be
    generated (e.g., paper.md -> paper.docx).

Design goal — cross-machine portability:
    This script depends ONLY on the Python 3 standard library (zipfile + xml +
    struct). It does NOT require third-party packages such as python-docx,
    markdown, or Pillow, nor does it need network access for installation. As
    long as a Python 3 interpreter is available on the target machine, the
    script runs without any environment or path restrictions.

Supported Markdown:
    Headings (# ~ ######), paragraphs, **bold**, *italic*, `inline code`,
    fenced code blocks (```), ordered/unordered lists, pipe tables,
    blockquotes (>), horizontal rules (---), images (![alt](path)).

Typesetting style (aligned with the paper-typesetting-academic skill):
    Body      : Chinese SimSun + Western Times New Roman, 12pt, justified,
                1.5 line spacing, 6pt spacing before/after.
    Headings  : Chinese SimHei + Western Times New Roman, bold,
                H1=16pt H2=13pt H3=12pt H4~H6=12pt, left aligned.
                Auto-numbered (1., 1.1, 1.1.1 ...) since v0.8.7.
    Page      : A4, 2.54 cm margins on all sides.
    Italic    : *italic* in markdown is preserved as italic in Word (for
                statistical symbols like F / P / χ² etc.).
    Tables    : Journal-style three-line table (top line, header line, bottom
                line), no vertical lines, no horizontal lines between data rows,
                table headers bold, cell text 10pt.
    Figure/Table captions: 10pt italic, centered; figures and tables are
                automatically numbered sequentially (Figure 1 / Table 1).
    Images    : ![caption](path) embedded in the document (width ≤ 14 cm,
                scaled proportionally); the caption becomes the figure title;
                if no caption is provided, "Figure N." is auto-generated.
    Code blocks: Consolas 10pt, line breaks preserved.

Dependencies: None (Python 3.8+ standard library only).
====================================================================
"""
import sys
import io
import re
import struct
import zipfile

# ------------------------------------------------------------------ Constants
BODY_CN, BODY_EN = "SimSun", "Times New Roman"      # Body Chinese / Western font
HEAD_CN, HEAD_EN = "SimHei", "Times New Roman"      # Heading Chinese / Western font
CODE_EN = "Consolas"
BODY_SIZE = 12
HEAD_SIZE = {1: 16, 2: 13, 3: 12, 4: 12, 5: 11, 6: 11}
MARGIN_TWIPS = 1440          # 2.54cm = 1440 twips (1 inch = 1440 twips)
A4_W, A4_H = 11906, 16838    # A4 in twips
LIST_INDENT = 420            # 0.74cm
LIST_HANG = -300             # Hanging indent
LINE_15 = 360                # 1.5 line spacing = 240 * 1.5
SPACE_6 = 120                # 6pt
CAP_SIZE = 10                # Caption font size
TBL_SIZE = 10                # Table text font size
MAX_W_EMU = 5040000          # Max image width 14cm (1cm = 360000 EMU)


# ------------------------------------------------------------------ XML helpers
def esc(s):
    """Escape XML reserved characters."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def xml_decl():
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


# ------------------------------------------------------------------ Inline parsing
# Match **bold** / *italic* / `inline code`
_INLINE_RE = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`')


def parse_inline(s):
    """Split a text into (text, bold, italic, code) tuples."""
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
    """Generate a <w:r> fragment."""
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
    """Convert text (possibly containing **/*/`) into runs XML."""
    out = []
    for (t, b, it, c) in parse_inline(text):
        if t == '':
            continue
        out.append(run_xml(t, bold=(base_bold or b), italic=(base_italic or it),
                           code=c, cn=cn, en=en, sz=sz))
    return ''.join(out)


def para_xml(runs_xml, align='both', sb=SPACE_6, sa=SPACE_6, line=LINE_15,
             indent=None, first_line=None):
    """Generate a <w:p> paragraph."""
    ppr = ['<w:pPr>']
    ppr.append(f'<w:jc w:val="{align}"/>')
    if indent is not None:
        fl = first_line if first_line is not None else 0
        ppr.append(f'<w:ind w:left="{indent}" w:firstLine="{fl}"/>')
    ppr.append(f'<w:spacing w:before="{sb}" w:after="{sa}" '
               f'w:line="{line}" w:lineRule="auto"/>')
    ppr.append('</w:pPr>')
    return f'<w:p>{"".join(ppr)}{runs_xml}</w:p>'


# ------------------------------------------------------------------ Block-level rendering
def render_heading(content, level, prefix=''):
    if prefix:
        content = f'{prefix} {content}'
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


def render_caption(text):
    """Figure/Table caption: 10pt italic, centered. text already contains number prefix."""
    runs = runs_for(text, base_bold=False, base_italic=True, sz=CAP_SIZE)
    return para_xml(runs, align='center', sb=SPACE_6, sa=SPACE_6, line=240)


def render_hr():
    """'---' horizontal rules are removed from the output (no visible line)."""
    return ''


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


# ------------------------------------------------------------------ Three-line table
def render_table_threeline(rows):
    """rows: list[list[str]], first row is header. Produces a journal three-line table."""
    if not rows:
        return ''
    ncols = max(len(r) for r in rows)
    nrows = len(rows)
    # Default borders are none (three lines are drawn by row borders)
    borders = ('<w:tblBorders>'
               '<w:top w:val="none"/><w:left w:val="none"/>'
               '<w:bottom w:val="none"/><w:right w:val="none"/>'
               '<w:insideH w:val="none"/><w:insideV w:val="none"/>'
               '</w:tblBorders>')
    tblpr = (f'<w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}'
             '<w:tblLook w:val="04A0"/></w:tblPr>')
    grid = '<w:tblGrid>' + ''.join('<w:gridCol w:w="3000"/>' for _ in range(ncols)) + '</w:tblGrid>'
    trs = []
    for ri, row in enumerate(rows):
        is_head = (ri == 0)
        is_last = (ri == nrows - 1)
        # Row borders: header row gets top line (sz=12) + header separator (sz=8); last row gets bottom line (sz=12)
        rb = ['<w:trBorders>']
        if is_head:
            rb.append('<w:top w:val="single" w:sz="12" w:space="0" w:color="000000"/>')
            rb.append('<w:bottom w:val="single" w:sz="8" w:space="0" w:color="000000"/>')
        if is_last:
            rb.append('<w:bottom w:val="single" w:sz="12" w:space="0" w:color="000000"/>')
        rb.append('</w:trBorders>')
        trpr = f'<w:trPr>{"".join(rb)}</w:trPr>'
        cells = []
        for ci in range(ncols):
            cell_text = row[ci] if ci < len(row) else ''
            cell_runs = runs_for(cell_text, base_bold=is_head, sz=TBL_SIZE)
            cell_p = (f'<w:p><w:pPr><w:spacing w:before="40" w:after="40" '
                      f'w:line="240" w:lineRule="auto"/></w:pPr>{cell_runs}</w:p>')
            tc = (f'<w:tc><w:tcPr><w:tcMar>'
                  f'<w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
                  f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>'
                  f'</w:tcMar></w:tcPr>{cell_p}</w:tc>')
            cells.append(tc)
        trs.append(f'<w:tr>{trpr}{"".join(cells)}</w:tr>')
    return f'<w:tbl>{tblpr}{grid}{"".join(trs)}</w:tbl>'


# ------------------------------------------------------------------ Image embedding
_IMG_RE = re.compile(r'^!\[(.*?)\]\(\s*([^)\s]+)\s*\)$')
_PNG_SIG = b'\x89PNG\r\n\x1a\n'
_CT_MAP = {'png': 'image/png', 'jpeg': 'image/jpeg', 'gif': 'image/gif'}


def get_image_dims(path):
    """Parse PNG/JPEG/GIF dimensions without Pillow; returns (w, h, fmt) or None."""
    try:
        with io.open(path, 'rb') as f:
            head = f.read(32)
    except OSError:
        return None
    if head[:8] == _PNG_SIG:
        if len(head) >= 24:
            w, h = struct.unpack('>II', head[16:24])
            return w, h, 'png'
        return None
    if head[:6] in (b'GIF87a', b'GIF89a'):
        w, h = struct.unpack('<HH', head[6:10])
        return w, h, 'gif'
    if head[:2] == b'\xff\xd8':
        try:
            with io.open(path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        i = 2
        n = len(data)
        while i < n - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack('>HH', data[i + 5:i + 9])
                return w, h, 'jpeg'
            if i + 4 > n:
                break
            seglen = struct.unpack('>H', data[i + 2:i + 4])[0]
            i += 2 + seglen
        return None
    return None


def image_drawing_xml(rel_id, w_emu, h_emu, doc_pr_id, pic_id):
    """Generate <w:p> with embedded image (centered)."""
    extent = f'<wp:extent cx="{w_emu}" cy="{h_emu}"/>'
    drawing = (
        f'<w:p><w:pPr><w:jc w:val="center"/>'
        f'<w:spacing w:before="120" w:after="60" w:line="240" w:lineRule="auto"/>'
        f'</w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0" '
        f'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'{extent}<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{doc_pr_id}" name="Picture {doc_pr_id}"/>'
        f'<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{pic_id}" name="img.png"/>'
        f'<pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w_emu}" cy="{h_emu}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        f'</pic:pic></a:graphicData></a:graphic>'
        f'</wp:inline></w:drawing></w:r></w:p>'
    )
    return drawing


# ------------------------------------------------------------------ Figure/Table caption numbering
_CAP_FIG_RE = re.compile(r'^(图|Fig\.?|Figure)\b', re.I)
_CAP_TAB_RE = re.compile(r'^(表|Table)\b', re.I)
_CAP_LEAD_RE = re.compile(r'^(图|表|Fig\.?|Figure|Table)\s*\d*\.?\s*', re.I)


def is_caption(text):
    return bool(_CAP_FIG_RE.match(text) or _CAP_TAB_RE.match(text))


def make_caption(text, kind, counters):
    """Generate caption text with automatic numbering. kind: 'fig' | 'tab'."""
    counters[kind] += 1
    n = counters[kind]
    eng = bool(re.match(r'^(Fig|Figure|Table)\b', text, re.I))
    if kind == 'fig':
        prefix = f'Fig. {n}. ' if eng else f'图 {n}. '
    else:
        prefix = f'Table {n}. ' if eng else f'表 {n}. '
    stripped = _CAP_LEAD_RE.sub('', text)
    return prefix + stripped


# ------------------------------------------------------------------ Main parsing
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
    if _IMG_RE.match(stripped):
        return 'image'
    if stripped in ('---', '***', '___'):
        return 'hr'
    if _LIST_RE.match(stripped):
        return 'list'
    return 'para'


def convert(md_text, base_dir='', media=None, counters=None):
    if media is None:
        media = []
    if counters is None:
        counters = {'fig': 0, 'tab': 0}
    lines = md_text.split('\n')
    n = len(lines)
    blocks = []
    img_seq = 0
    doc_pr_id = 1
    pic_id = 1
    hcnt = [0, 0, 0, 0, 0, 0, 0]   # heading counters for levels 1-6
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Code block
        if stripped.startswith('```'):
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            blocks.append(render_code_block('\n'.join(code_lines)))
            continue

        # Heading
        if stripped.startswith('#'):
            level = len(stripped) - len(stripped.lstrip('#'))
            level = max(1, min(6, level))
            content = stripped[level:].strip()
            # Reset counters for all deeper levels, increment current level
            for lv in range(level + 1, 7):
                hcnt[lv] = 0
            hcnt[level] += 1
            prefix = '.'.join(str(hcnt[lv]) for lv in range(1, level + 1)) + '.'
            blocks.append(render_heading(content, level, prefix))
            i += 1
            continue

        # Image
        m_img = _IMG_RE.match(stripped)
        if m_img:
            alt, src = m_img.group(1).strip(), m_img.group(2).strip()
            path = src if os_path_isabs(src) else os_path_join(base_dir, src)
            # Images are always numbered as figures
            counters['fig'] += 1
            fig_no = counters['fig']
            # Embed image
            dims = get_image_dims(path)
            if dims is not None:
                w_px, h_px, fmt = dims
                if w_px and h_px:
                    w_emu = MAX_W_EMU
                    h_emu = int(MAX_W_EMU * h_px / w_px)
                else:
                    w_emu = h_emu = MAX_W_EMU
                try:
                    with io.open(path, 'rb') as fb:
                        data = fb.read()
                except OSError:
                    data = None
                if data is not None:
                    img_seq += 1
                    rid = f'rId{img_seq}'
                    ext = 'jpg' if fmt == 'jpeg' else fmt
                    media.append({'rid': rid, 'ext': ext, 'fname': f'image{img_seq}.{ext}',
                                  'data': data})
                    blocks.append(image_drawing_xml(rid, w_emu, h_emu, doc_pr_id, pic_id))
                    doc_pr_id += 1
                    pic_id += 1
            # Caption
            eng = bool(re.match(r'^(Fig|Figure)\b', alt, re.I))
            cap_prefix = f'Fig. {fig_no}. ' if eng else f'图 {fig_no}. '
            cap_text = _CAP_LEAD_RE.sub('', alt) if alt else ''
            blocks.append(render_caption(cap_prefix + cap_text))
            i += 1
            continue

        # Blockquote / Caption
        if stripped.startswith('>'):
            q_lines = []
            while i < n and lines[i].strip().startswith('>'):
                q_lines.append(lines[i].strip()[1:].strip())
                i += 1
            text = ' '.join(q_lines)
            if is_caption(text):
                kind = 'fig' if _CAP_FIG_RE.match(text) else 'tab'
                blocks.append(render_caption(make_caption(text, kind, counters)))
            else:
                blocks.append(render_quote(text))
            continue

        # Horizontal rule
        if stripped in ('---', '***', '___'):
            blocks.append(render_hr())
            i += 1
            continue

        # Table (line contains '|' and next line is a separator)
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
            blocks.append(render_table_threeline(rows))
            continue

        # List
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

        # Normal paragraph (aggregate consecutive non-blank, non-special lines)
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

        # Blank line
        i += 1

    # Assemble document.xml
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


# ------------------------------------------------------------------ Path helpers (avoid importing os fully)
def os_path_isabs(p):
    return p.startswith('/') or (len(p) > 1 and p[1] == ':')


def os_path_join(base, name):
    import os
    return os.path.join(base, name) if base else name


# ------------------------------------------------------------------ Pack docx
def build_docx(document_xml, media, dst):
    defaults = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    seen = set()
    for m in media:
        if m['ext'] not in seen:
            seen.add(m['ext'])
            ct = _CT_MAP.get(m['ext'], 'image/png')
            defaults.append(f'<Default Extension="{m["ext"]}" ContentType="{ct}"/>')
    content_types = (xml_decl() +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + ''.join(defaults) +
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (xml_decl() +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>')
    doc_rels_items = ''.join(
        f'<Relationship Id="{m["rid"]}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/{m["fname"]}"/>'
        for m in media)
    doc_rels = (xml_decl() +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + doc_rels_items + '</Relationships>')
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', document_xml)
        if media:
            z.writestr('word/_rels/document.xml.rels', doc_rels)
            for m in media:
                z.writestr(f'word/media/{m["fname"]}', m['data'])


# ------------------------------------------------------------------ Entry point
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    # utf-8-sig auto-strips BOM to avoid breaking the opening # heading
    with io.open(src, encoding='utf-8-sig') as f:
        md_text = f.read()
    if len(sys.argv) >= 3:
        dst = sys.argv[2]
    else:
        dst = re.sub(r'\.md$', '.docx', src, flags=re.I)
    import os
    base_dir = os.path.dirname(os.path.abspath(src))
    media = []
    counters = {'fig': 0, 'tab': 0}
    document_xml = convert(md_text, base_dir=base_dir, media=media, counters=counters)
    build_docx(document_xml, media, dst)
    print(f"✓ Converted: {src} -> {dst} (images: {len(media)}, figures: {counters['fig']}, tables: {counters['tab']})")


if __name__ == '__main__':
    main()