# -*- coding: utf-8 -*-
"""OCR 引擎：将扫描版 PDF 转为 Markdown，支持文字层 PDF 与扫描版混合处理。"""
import io
import re
from pathlib import Path

from pypdf import PdfReader
from PIL import Image
import numpy as np
from rapidocr_onnxruntime import RapidOCR


try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


HEADING_RE = re.compile(
    r'^(第[零一二三四五六七八九十百千万0-9]+[章节回卷部篇]|'
    r'目录|内容提要|前言|序言|自序|代序|后记|引子|楔子)'
)
NUM_SECTION = set('一二三四五六七八九十')
DIGIT_RE = re.compile(r'\d')
OCR_NUMBERED_HEADING_RE = re.compile(
    r'^(?:\d{1,2}(?:\.\d{1,2})?)[、.．]?\s*[\u4e00-\u9fff].{0,40}$'
)

# 页面已有文字层时，若单页可提取字符数超过此阈值，优先直接提取文字
TEXT_LAYER_THRESHOLD = 100


def _text_layer_is_reliable(text: str) -> bool:
    """判断清洗后的文字层是否足够可靠，避免把明显乱码当作正常文本。"""
    text = _clean_text_layer(text)
    if len(text) < TEXT_LAYER_THRESHOLD:
        return False
    compact = re.sub(r'\s+', '', text)
    if not compact:
        return False
    duplicate_pairs = sum(
        1 for a, b in zip(compact, compact[1:])
        if a == b and CJK_RE.match(a)
    )
    duplicate_ratio = duplicate_pairs / max(len(compact) - 1, 1)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_ratio = sum(len(line) <= 2 for line in lines) / max(len(lines), 1)
    return duplicate_ratio < 0.18 and short_ratio < 0.72

# 部分 PDF 用“同一字形偏移绘制两次”模拟粗体，文字提取后出现相邻重复字符
# （如“文文件件密密级级”），需要折叠还原
CJK_RE = re.compile(r'[\u4e00-\u9fff]')
CJK_FW_RE = re.compile(r'[\u4e00-\u9fff\uff00-\uffef]')
# 中文/全角标点之间只隔 1 个空格的，属于伪粗体双写残留（如“开 票”）；
# 表格列之间是多个空格，不受影响（只匹配单个空格）
CJK_SPACED_RE = re.compile(r'([\u4e00-\u9fff\uff00-\uffef]) (?=[\u4e00-\u9fff\uff00-\uffef])')
# 粗体行中残留的相邻重复中文/全角字符（如“开开”“第第”）
CJK_DOUBLE_RE = re.compile(r'([\u4e00-\u9fff\uff00-\uffef])\1')
# 标题编号伪粗体：编号+句点被绘制两次，如“1.1.”应为“1.”、“2.2.”应为“2.”
NUM_DOUBLE_RE = re.compile(r'(\d+)\.\1\.')


def _clean_line(line: str):
    """清洗单行。返回 (清洗后文本, 是否为伪粗体行)。

    伪粗体 PDF 把同一字形偏移绘制两次，提取后出现连续双写字符
    （如“文文件件密密级级”→“文件密级”）。
    判定规则：行中出现至少 2 组连续的“XXYY”双写片段才视为粗体行，
    正常文本中的偶合（如日期 2022、版本号 1.0.11）不会被误伤。
    """
    chars = list(line)
    n = len(chars)
    remove = set()
    is_bold_line = False

    k = 0
    while k < n:
        # 从 k 开始统计连续双写片段：chars[k]==chars[k+1]，步长 2 向后延伸
        run_pairs = 0
        p = k
        while p + 1 < n and not chars[p].isspace() and chars[p] == chars[p + 1]:
            run_pairs += 1
            p += 2
        if run_pairs >= 2:
            is_bold_line = True
            q = k
            while q + 1 < n and not chars[q].isspace() and chars[q] == chars[q + 1]:
                remove.add(q + 1)
                q += 2
            k = q
        else:
            k += 1

    if is_bold_line:
        line = ''.join(ch for idx, ch in enumerate(chars) if idx not in remove)
        # 先并掉中文间的单个残留空格（如“密 密级”→“密密级”），
        # 再折叠相邻重复中文/全角字符（“密密”→“密”）
        line = CJK_SPACED_RE.sub(r'\1', line)
        line = CJK_DOUBLE_RE.sub(r'\1', line)
    return line, is_bold_line


def _is_bold_continuation(line: str) -> bool:
    """检测被 layout 提取拆到下一行的伪粗体残片。

    特征：行主体是空白，只留一个由相邻重复中文/全角字符组成的小尾巴
    （如表格头“域类型”被拆成“域域类类”+“ 型型”）。
    """
    stripped = line.strip()
    if not stripped:
        return False
    # 非空白字符占比要低（本质是被空白推过去的残片）
    if len(stripped) / max(len(line), 1) > 0.35:
        return False
    # 残片内容应为相邻重复字符：XX 或 XXXX…
    if len(stripped) < 2 or len(stripped) % 2 != 0:
        return False
    for i in range(0, len(stripped), 2):
        if stripped[i] != stripped[i + 1]:
            return False
        if not CJK_FW_RE.match(stripped[i]):
            return False
    return True


def _fold_bold_token(line: str) -> str:
    """折叠行中伪粗体残片，如“型型”→“型”。"""
    stripped = line.strip()
    return ''.join(stripped[i] for i in range(0, len(stripped), 2))


def _clean_text_layer(text: str) -> str:
    """清洗文字层提取结果：折叠伪粗体双写、合并跨行的粗体残片、去掉粗体
    行中中文间单个多余空格、压缩封面等页面过多的空行。"""
    lines = text.split('\n')
    cleaned = [_clean_line(line)[0] for line in lines]

    # 合并跨行的粗体残片（如“域域类类”+“型型”→“域类型”）
    merged = []
    last_nonempty_idx = -1
    for line in cleaned:
        if merged and _is_bold_continuation(line):
            token = _fold_bold_token(line)
            if last_nonempty_idx >= 0 and CJK_FW_RE.match(merged[last_nonempty_idx][-1]):
                merged[last_nonempty_idx] = merged[last_nonempty_idx] + token
            else:
                merged.append(line)
                if line.strip():
                    last_nonempty_idx = len(merged) - 1
        else:
            merged.append(line)
            if line.strip():
                last_nonempty_idx = len(merged) - 1
    cleaned = merged

    text = '\n'.join(cleaned)
    # 标题编号伪粗体（“1.1.”→“1.”），仅匹配 编号.同编号. 形式，不影响
    # 正常的章节号（如 1.2）和版本号（1.0.11）
    text = NUM_DOUBLE_RE.sub(r'\1.', text)
    # 3 个以上连续换行压缩为 2 个（封面大段留白）
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _is_header_token(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if DIGIT_RE.search(t):
        return True
    if len(t) <= 3 and all('\u4e00' <= ch <= '\u9fff' for ch in t):
        return True
    return False


# ================= 文字版 PDF 语义化转换 =================
# 基于 pypdf visitor_text 字符坐标重建行/单元格，把表格转成 Markdown 表格、
# 加粗章节标题转成 Markdown 标题、代码示例转成代码块、普通段落正常输出。
_HEAD_NUM_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\.?\s+')
_CODE_TOKENS = ('.append(', 'StringBuilder', 'new StringBuilder', 'public ',
                'private ', 'Map<', 'List<')
_STD_HEADER_FIRST = ('元素名称',)
_STD_HEADER_SECOND = ('元素',)
_RETCODE_X_CODE = 170.9
_RETCODE_X_DESC = 292.5


def _md_dedup_chars(chars):
    """折叠伪粗体（同一字形偏移绘制两次）。chars: [(x, ch), ...] 已按 x 排序。"""
    if not chars:
        return chars
    out = [chars[0]]
    for x, ch in chars[1:]:
        last_x, last_ch = out[-1]
        gap = x - last_x
        if ch == last_ch and 0 < gap < 6:
            continue
        if ch == last_ch and CJK_RE.match(ch) and 0 < gap < 10:
            continue
        out.append((x, ch))
    return out


def _md_clean_cell(text):
    chars = list(text)
    n = len(chars)
    remove = set()
    k = 0
    while k < n:
        run_pairs = 0
        p = k
        while p + 1 < n and not chars[p].isspace() and chars[p] == chars[p + 1]:
            run_pairs += 1
            p += 2
        if run_pairs >= 2:
            q = k
            while q + 1 < n and not chars[q].isspace() and chars[q] == chars[q + 1]:
                remove.add(q + 1)
                q += 2
            k = q
        else:
            k += 1
    if remove:
        text = ''.join(ch for idx, ch in enumerate(chars) if idx not in remove)
    text = CJK_SPACED_RE.sub(r'\1', text)
    text = CJK_DOUBLE_RE.sub(r'\1', text)
    text = NUM_DOUBLE_RE.sub(r'\1.', text)
    return text.strip()


def _md_extract_line_cells(page, y_tol=3.5, col_gap_px=25):
    """用 visitor_text 提取字符坐标，聚类成行，再按 x 间隙拆成单元格。
    返回 [(y, [(text, x0, x1), ...]), ...]（本 PDF y 向下增大，y 升序=阅读顺序）。"""
    records = []

    def visitor(text, cm, tm, font_dict, font_size):
        x, y = tm[4], tm[5]
        for ch in text:
            records.append((y, x, ch))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return []
    records = [(y, x, ch) for y, x, ch in records if y > 1]
    if not records:
        return []
    records.sort(key=lambda t: (t[0], t[1]))

    rows = []
    for y, x, ch in records:
        if not rows or abs(y - rows[-1][0]) > y_tol:
            rows.append([y, []])
        rows[-1][1].append((x, ch))

    result = []
    for y, chars in rows:
        chars.sort(key=lambda t: t[0])
        chars = _md_dedup_chars(chars)
        cells = []
        if chars:
            start_x, start_i = chars[0][0], 0
            for i in range(1, len(chars)):
                if chars[i][0] - chars[i - 1][0] > col_gap_px:
                    cells.append((''.join(c for _, c in chars[start_i:i]),
                                  start_x, chars[i - 1][0]))
                    start_i, start_x = i, chars[i][0]
            cells.append((''.join(c for _, c in chars[start_i:]),
                          start_x, chars[-1][0]))
        result.append((y, cells))
    return result


def _md_line_text(cells):
    return ' '.join(t for t, _, _ in cells if t.strip())


def _md_render_items(page):
    """返回 [(y, line_text, cells), ...]，单元格已清洗。"""
    out = []
    for y, cells in _md_extract_line_cells(page):
        cleaned = [(_md_clean_cell(t), x0, x1) for t, x0, x1 in cells]
        cleaned = [(t, x0, x1) for t, x0, x1 in cleaned if t]
        if cleaned:
            out.append((y, _md_line_text(cleaned), cleaned))
    return out


_FIRST_COL_RIGHT = 195


def _md_merge_continuations(items):
    """把单单元格续行碎片（描述/字段名换行）合并回上一行对应单元格。"""
    merged = []
    for y, text, cells in items:
        merged.append((y, text, cells))
        if not text.strip() or len(cells) != 1 or _md_is_code(text):
            continue
        ctext, cx0, cx1 = cells[0]
        target_idx = -1
        if cx0 > _FIRST_COL_RIGHT:
            if len(merged) >= 2:
                prev_cells = merged[-2][2]
                for idx, (_pt, px0, px1) in enumerate(prev_cells):
                    if px0 - 10 <= cx0 <= px1 + 30:
                        target_idx = idx
                        break
        else:
            s = ctext.strip()
            if (len(s) <= 6 and CJK_FW_RE.match(s)
                    and all('\u4e00' <= c <= '\u9fff' for c in s)
                    and len(merged) >= 2 and len(merged[-2][2]) >= 2):
                target_idx = 0
        if target_idx >= 0:
            prev_y, _pt, prev_cells = merged[-2]
            new_cells = list(prev_cells)
            ptext, px0, px1 = new_cells[target_idx]
            joiner = ''
            if ptext and ctext:
                pc, nc = ptext.rstrip()[-1], ctext.lstrip()[0]
                if not (CJK_RE.match(pc) and CJK_RE.match(nc)
                        or CJK_FW_RE.match(pc) and CJK_FW_RE.match(nc)):
                    joiner = ' '
            new_cells[target_idx] = (ptext + joiner + ctext, px0, max(px1, cx1))
            merged[-2] = (prev_y, _md_line_text(new_cells), new_cells)
            merged.pop()
    return merged


def _md_is_code(line):
    return any(tok in line for tok in _CODE_TOKENS)


def _md_is_numbered_heading(text, cells=None):
    s = text.strip()
    if len(s) > 25:
        return False
    # 多格行是表格数据（如 '100' + '北京'），不是章节标题；
    # 章节标题正文行为单格、且顶格(x0<145)
    if cells is not None:
        if len(cells) > 1:
            return False
        x0 = cells[0][1] if cells else 0
        if x0 >= 145:
            return False
    m = _HEAD_NUM_RE.match(s)
    if not m or not re.match(r'^\d+$', m.group(1)):
        return False
    rest = s[m.end():].strip()
    if not rest or not CJK_RE.match(rest):
        return False
    if any(tok in s for tok in ('Max', 'List', 'object', 'list')):
        return False
    return True


def _md_is_section_label(text):
    s = text.strip()
    if len(s) > 30:
        return False
    return s.endswith('说明') or s.endswith('信息') or s == '文档状态'


def _md_is_std_header(cells):
    if len(cells) < 5:
        return False
    return (cells[0][0].strip().replace(' ', '') in _STD_HEADER_FIRST
            and cells[1][0].strip() in _STD_HEADER_SECOND)


def _md_is_small_header(cells):
    if len(cells) != 3:
        return False
    for t, _x0, _x1 in cells:
        s = t.strip()
        if not s or len(s) > 6 or not all('\u4e00' <= c <= '\u9fff' for c in s):
            return False
    return True


def _md_is_table_header(text, cells):
    if _md_is_code(text) or _md_is_numbered_heading(text) or _md_is_section_label(text):
        return False
    return _md_is_std_header(cells) or _md_is_small_header(cells)


def _md_is_data_row(cells):
    if len(cells) < 2:
        return False
    first = cells[0][0].strip()
    if not first:
        return False
    # 首格为短内容：中文/英文标识符/纯数字编码（如地区编码 100、1200）
    if len(first) > 24:
        return False
    return True


def _md_row_is_short_cells(cells, max_len=16):
    """一行由若干'短'单元格组成（表格数据特征，区别于长段落/代码）。"""
    if len(cells) < 2:
        return False
    for t, _x0, _x1 in cells:
        s = t.strip()
        if not s or len(s) > max_len:
            return False
    return True


def _md_detect_grid_table(stream, i):
    """从流位置 i 起，探测一个'结构一致的多列数据块'（用于识别没有规范
    表头的简单对照表，如 地区编码|地区名称）。
    返回 (header_cells, data_cells_list, end_stream_idx) 或 None。
    规则：连续若干行单元格数一致(>=2)、每行短格、各列 x0 稳定对齐，行数>=3。
    接口字段表（含 Max(..)/域类型 M/C/O 尾列）不属于此类，整体放弃。"""
    block = []  # (stream_idx, cells)
    ncol = None
    j = i
    while j < len(stream):
        if stream[j][0] != 'line':
            j += 1
            continue
        _p, text, cells = stream[j][1]
        if (_md_is_code(text) or _md_is_numbered_heading(text, cells)
                or _md_is_section_label(text) or _md_is_retcode_code(cells)):
            break
        if len(cells) < 2 or not _md_row_is_short_cells(cells):
            break
        if ncol is None:
            ncol = len(cells)
        elif len(cells) != ncol:
            break
        block.append((j, cells))
        j += 1

    if len(block) < 3 or ncol is None or ncol < 2:
        return None

    # 整体排除接口字段表：任一行含 Max( 长度列，或尾列为域类型 M/C/O
    for _idx, cells in block:
        row_text = ' '.join(t for t, _x, _y in cells)
        if 'Max(' in row_text:
            return None
        last_t = cells[-1][0].strip()
        if last_t in ('M', 'C', 'O', 'MS', 'OS') and ncol >= 4:
            return None

    # 校验列对齐：同列 x0 跨度 <=30px
    col_x = [[] for _ in range(ncol)]
    for _idx, cells in block:
        for ci in range(ncol):
            col_x[ci].append(cells[ci][1])
    for xs in col_x:
        if max(xs) - min(xs) > 30:
            return None

    def is_label(t):
        s = t.strip()
        if not s or len(s) > 8:
            return False
        # 表头标签：短、含中文、不含字母数字编码
        return bool(CJK_RE.search(s)) and not re.search(r'[A-Za-z0-9]', s)

    first_idx = block[0][0]
    first_cells = block[0][1]

    # 向上回溯一行：若紧邻的上一 line 是同列数的中文标签行，作为表头纳入
    k = first_idx - 1
    while k >= 0 and stream[k][0] != 'line':
        k -= 1
    header = None
    if k >= 0:
        _kp, _kt, kcells = stream[k][1]
        if (len(kcells) == ncol
                and all(is_label(t) for t, _x, _y in kcells)
                and not _md_is_section_label(_kt)):
            header = kcells

    if header is not None:
        data = [c for _idx, c in block]
        end_idx = block[-1][0]
    elif all(is_label(t) for t, _x, _y in first_cells):
        header = first_cells
        data = [c for _idx, c in block[1:]]
        end_idx = block[-1][0]
    else:
        # 无表头：占位表头
        header = [('列%d' % (c + 1), first_cells[c][1], first_cells[c][1])
                  for c in range(ncol)]
        data = [c for _idx, c in block]
        end_idx = block[-1][0]
    if not data:
        return None
    return header, data, end_idx


def _md_is_fragment(cells):
    if len(cells) != 1:
        return False
    s = cells[0][0].strip()
    if not s:
        return False
    if len(s) <= 8 and all('\u4e00' <= c <= '\u9fff' for c in s):
        return True
    if len(s) <= 20 and re.match(r'^[A-Za-z][A-Za-z0-9_]*$', s):
        return True
    return False


_FIELD_TYPE_TOKENS = {'M', 'C', 'O', 'MS', 'OS', 'CS', 'Y', 'N'}


def _md_is_field_row(text, cells):
    """接口字段表数据行：含 Max(..) 长度列，或尾列为域类型(M/C/O..)/签名标志(Y/N)，
    且列数>=4、首列为短中文元素名。"""
    if len(cells) < 4:
        return False
    if 'Max(' in text:
        return True
    last = cells[-1][0].strip()
    if last in _FIELD_TYPE_TOKENS:
        return True
    return False


def _md_detect_field_table(stream, i):
    """探测跨页/无规范表头的接口字段表块。
    返回 (cells_list_for_render, end_idx) 或 None。cells_list 第一行为已对齐的
    占位表头（元素名称/元素/长度/描述/域类型/...），其余为数据单元格。"""
    block = []
    j = i
    while j < len(stream):
        if stream[j][0] != 'line':
            j += 1
            continue
        _p, text, cells = stream[j][1]
        if _md_is_code(text) or _md_is_numbered_heading(text, cells) \
                or _md_is_section_label(text) or _md_is_retcode_code(cells):
            break
        if _md_is_field_row(text, cells):
            block.append((j, cells))
            j += 1
            continue
        # 字段行之间允许单格续行碎片（描述换行），先简单跳过不纳入
        break

    if len(block) < 2:
        return None

    # 用全部行的 x0 聚类推导列锚点
    all_cells = [c for _idx, cells in block for c in cells]
    xs = sorted({round(x0, 1) for _t, x0, _x1 in all_cells})
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 45:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    anchors = [sum(c) / len(c) for c in clusters]
    n_cols = len(anchors)
    if n_cols < 3:
        return None

    # 占位表头：按列位置给通用名
    default_headers = ['元素名称', '元素', '长度', '描述', '域类型', '签名', '列7', '列8']
    header = [(default_headers[k] if k < len(default_headers) else '列%d' % (k + 1),
               anchors[k], anchors[k]) for k in range(n_cols)]

    rows = []
    for _idx, cells in block:
        row = [''] * n_cols
        for t, x0, _x1 in cells:
            col = min(range(n_cols), key=lambda c: abs(anchors[c] - x0))
            row[col] = (row[col] + ' ' + t).strip() if row[col] else t
        rows.append(row)

    cells_list = [header] + [[(r[c], anchors[c], anchors[c]) for c in range(n_cols)]
                             for r in rows]
    return cells_list, block[-1][0]


def _md_is_retcode_code(cells):
    if len(cells) != 1:
        return False
    t, x0, _ = cells[0]
    return abs(x0 - _RETCODE_X_CODE) < 15 and t.strip().isdigit()


def _md_is_retcode_desc(cells):
    if len(cells) != 1:
        return False
    t, x0, _ = cells[0]
    return abs(x0 - _RETCODE_X_DESC) < 15 and CJK_RE.search(t) is not None


def _md_derive_columns(cells_list, n_cols):
    """从表格全部行的单元格 x0 聚类推导列（表头标签与数据可能不对齐）。"""
    xs = sorted({round(x0, 1) for cells in cells_list for _t, x0, _x1 in cells})
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 40:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    def rowsets():
        rs = []
        for cells in cells_list:
            idxs = set()
            for _t, x0, _x1 in cells:
                for ci, c in enumerate(clusters):
                    if c[0] - 1 <= x0 <= c[-1] + 1:
                        idxs.add(ci)
                        break
            rs.append(idxs)
        return rs

    while len(clusters) > n_cols:
        rss = rowsets()
        merge_at = -1
        for a in range(len(clusters) - 1):
            if not any(a in r and (a + 1) in r for r in rss):
                merge_at = a
                break
        if merge_at < 0:
            merge_at = 0
        clusters[merge_at] = clusters[merge_at] + clusters[merge_at + 1]
        del clusters[merge_at + 1]
    return sorted((x, ci) for ci, c in enumerate(clusters) for x in c)


def _md_cells_to_row(cells, member_col, n_cols):
    row = [''] * n_cols
    for t, x0, _x1 in cells:
        col = min(member_col, key=lambda mc: abs(mc[0] - x0))[1]
        if col >= n_cols:
            col = n_cols - 1
        row[col] = (row[col] + ' ' + t) if row[col] else t
    return row


def _md_normalize_table_rows(rows):
    """统一接口字段表列数，并将域类型 token 放入最后一列。"""
    if not rows:
        return rows
    header = rows[0]
    standard = len(header) >= 5 and header[0].replace(' ', '') == '元素名称'
    if not standard:
        return rows
    n_cols = len(header)
    normalized = [header[:n_cols]]
    for row in rows[1:]:
        row = list(row[:n_cols])
        row += [''] * (n_cols - len(row))
        token = row[-1].strip()
        if not token and len(row) >= 4:
            for idx in range(n_cols - 2, 1, -1):
                if row[idx].strip() in _FIELD_TYPE_TOKENS:
                    row[-1] = row[idx]
                    row[idx] = ''
                    break
        normalized.append(row)
    return normalized


def _md_render_rows(rows):
    rows = _md_normalize_table_rows(rows)
    n_cols = max(len(r) for r in rows)
    rows = [r + [''] * (n_cols - len(r)) for r in rows]
    rows = [[str(cell).replace('|', '\\|') for cell in row] for row in rows]
    md = ['| ' + ' | '.join(r) + ' |' for r in rows]
    md.insert(1, '| ' + ' | '.join(['---'] * n_cols) + ' |')
    return '\n'.join(md)


def _md_render_table(cells_list):
    if not cells_list:
        return ''
    if isinstance(cells_list[0][0], str):
        return _md_render_rows(cells_list)
    n_cols = len(cells_list[0])
    member_col = _md_derive_columns(cells_list, n_cols)
    rows = [_md_cells_to_row(c, member_col, n_cols) for c in cells_list]
    return _md_render_rows(rows)


def _md_norm_bookmark_title(raw):
    """标准化书签标题：去多余空格，把 '2开票- 发票 开具' 规范化为 '2. 发票开具'。"""
    s = re.sub(r'\s+', '', raw or '')
    s = s.replace('（', '(').replace('）', ')')
    m = re.match(r'^(\d+)\.?\s*(.*)$', s)
    if m:
        num, rest = m.group(1), m.group(2)
        rest = rest.lstrip('-—').strip()
        rest = re.sub(r'^开票[-—]?', '', rest)
        return (num + '. ' + rest).strip()
    return s


def _md_title_key(s):
    """归一化标题键：去空白/标点/数字，转小写，用于匹配。"""
    return re.sub(r'[\s\.\-—:：()（）0-9]+', '', s).lower()


def _md_extract_bookmarks(reader):
    """从 PDF 大纲提取书签：[(page_idx0基, level, clean_title), ...]。"""
    marks = []

    def walk(items, depth=0):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                pidx = reader.get_destination_page_number(it)
            except Exception:
                pidx = None
            if pidx is not None:
                marks.append((pidx, min(depth, 2), _md_norm_bookmark_title(it.title)))

    try:
        walk(reader.outline)
    except Exception:
        return []
    return marks


def _md_bookmark_core(title):
    """书签核心词：去编号、去括号说明。"""
    s = re.sub(r'^\d+\.\s*', '', title)
    s = re.sub(r'[（(].*$', '', s)
    return s.strip()


def _md_is_heading_like(text, cells):
    """标题样式行：编号标题 / 说明标签 / 短中文行。"""
    if _md_is_numbered_heading(text, cells) or _md_is_section_label(text):
        return True
    s = text.strip()
    if len(s) <= 24 and CJK_RE.search(s) and not _md_is_code(s):
        if not re.search(r'Max|List|object|http|/|\.', s):
            return True
    return False


def _md_build_stream(reader, page_payloads=None):
    """流：[('pb', page_idx) | ('line', (page_idx, text, cells)), ...]。"""
    stream = []
    for pidx, page in enumerate(reader.pages):
        if page_payloads is not None and pidx in page_payloads:
            for y, left_x, text in page_payloads[pidx]:
                cells = [(text, left_x, left_x + max(len(text), 1))]
                stream.append(('line', (pidx, text, cells)))
        else:
            for _y, text, cells in _md_merge_continuations(_md_render_items(page)):
                if text.strip():
                    stream.append(('line', (pidx, text, cells)))
        stream.append(('pb', pidx))
    return stream


def convert_text_pdf(reader, page_payloads=None):
    """把文字版 PDF（pypdf PdfReader）语义化转换为 Markdown。
    表格 -> Markdown 表格；PDF 书签 -> 目录 + 章节标题；代码 -> 代码块。"""
    stream = _md_build_stream(reader, page_payloads)
    marks = _md_extract_bookmarks(reader)

    # 目录
    toc = []
    for _p, level, title in marks:
        toc.append('  ' * level + '- ' + title)

    # ---- 第一遍：为每个书签在正文中定位标题行（流索引）----
    match_at = [None] * len(marks)
    used = set()
    for mi, (mp, _lv, title) in enumerate(marks):
        ck = _md_title_key(_md_bookmark_core(title))
        if not ck or len(ck) < 2:
            continue
        cands = []
        for idx, (k, pl) in enumerate(stream):
            if k != 'line' or idx in used:
                continue
            lpage, ltext, lcells = pl
            if lpage < mp or lpage > mp + 6:
                continue
            is_num = _md_is_numbered_heading(ltext, lcells)
            if not (is_num or _md_is_heading_like(ltext, lcells)):
                continue
            if not is_num and (_md_is_data_row(lcells) or _md_is_table_header(ltext, lcells)):
                continue
            key = _md_title_key(ltext)
            if ck in key or key in ck:
                cands.append((lpage, idx))
        if cands:
            cands.sort()
            match_at[mi] = cands[-1][1]
            used.add(cands[-1][1])

    # 未匹配书签 -> 在其书签页首个 line 处注入
    inject_at = {}
    for mi, (mp, _lv, title) in enumerate(marks):
        if match_at[mi] is not None:
            continue
        for idx, (k, pl) in enumerate(stream):
            if k == 'line' and pl[0] == mp:
                inject_at.setdefault(idx, []).append(mi)
                break

    def is_suppressed(text, cells, stream_idx):
        """与某书签核心词匹配、但不是正式命中行的标题样式行 -> 抑制（目录条目/重复标题）。"""
        if len(cells) > 1:
            return False
        if not (_md_is_numbered_heading(text) or _md_is_heading_like(text, cells)):
            return False
        if _md_is_code(text) or _md_is_retcode_code(cells):
            return False
        if _md_is_table_header(text, cells) or _md_is_data_row(cells):
            return False
        key = _md_title_key(text)
        if not key:
            return False
        for mi, (_mp, _lv, title) in enumerate(marks):
            ck = _md_title_key(_md_bookmark_core(title))
            if not ck or len(ck) < 2:
                continue
            if ck in key or key in ck:
                return match_at[mi] != stream_idx
        return False

    # ---- 第二遍：渲染 ----
    out = []
    if toc:
        out.append('## 目录\n\n' + '\n'.join(toc))
    table = None

    def flush_table():
        nonlocal table
        if table is not None:
            body = [table['header']] + table['rows']
            if len(body) >= 2:
                out.append(_md_render_table(body))
            else:
                out.append(_md_line_text(table['header']))
            table = None

    def next_line(pos):
        j = pos + 1
        while j < len(stream) and stream[j][0] != 'line':
            j += 1
        return j

    i = 0
    n = len(stream)
    while i < n:
        if stream[i][0] == 'pb':
            i += 1
            continue
        pidx, text, cells = stream[i][1]

        if i in inject_at:
            flush_table()
            for mi in inject_at[i]:
                _mp, lv, title = marks[mi]
                out.append(('## ' if lv == 0 else '### ') + title)

        hit = [mi for mi in range(len(marks)) if match_at[mi] == i]
        if hit:
            flush_table()
            for mi in hit:
                _mp, lv, title = marks[mi]
                out.append(('## ' if lv == 0 else '### ') + title)
            i += 1
            continue

        if is_suppressed(text, cells, i):
            i += 1
            continue

        if _md_is_section_label(text):
            flush_table()
            out.append('**' + text.strip() + '**')
            i += 1
            continue

        # 网格表兜底：无规范表头、但结构一致的多列数据块（如地区编码对照表）。
        # 需先于编号标题判定，避免 '100 北京' 这类编码行被误判为章节标题。
        if table is None and len(cells) >= 2 and _md_row_is_short_cells(cells):
            grid = _md_detect_grid_table(stream, i)
            if grid is not None:
                gheader, gdata, gend = grid
                flush_table()
                out.append(_md_render_table([gheader] + gdata))
                i = gend + 1
                continue

        if _md_is_numbered_heading(text, cells):
            # 与已命中书签同核心词的编号标题（跨页重复）-> 跳过
            key = _md_title_key(text)
            dup = False
            for _mp, _lv, title in marks:
                ck = _md_title_key(_md_bookmark_core(title))
                if ck and len(ck) >= 2 and (ck in key or key in ck):
                    dup = True
                    break
            if not dup:
                flush_table()
                x0 = cells[0][1] if cells else 120
                out.append(('##' if x0 < 140 else '###') + ' ' + text.strip())
            i += 1
            continue

        if _md_is_code(text):
            flush_table()
            code = [text]
            i += 1
            while i < n:
                if stream[i][0] != 'line':
                    i += 1
                    continue
                next_text = stream[i][1][1]
                if _md_is_code(next_text) or re.match(r'^\s*[.}]', next_text):
                    code.append(next_text)
                    i += 1
                    continue
                break
            out.append('```java\n' + '\n'.join(code) + '\n```')
            continue

        if _md_is_retcode_code(cells):
            j = next_line(i)
            if j < n and _md_is_retcode_desc(stream[j][1][2]):
                flush_table()
                rows = [['返回码', '说明'],
                        [cells[0][0].strip(), stream[j][1][2][0][0].strip()]]
                j = next_line(j)
                while j < n:
                    _kp, kt, kc = stream[j][1]
                    if not _md_is_retcode_code(kc):
                        break
                    m2 = next_line(j)
                    if m2 < n and _md_is_retcode_desc(stream[m2][1][2]):
                        rows.append([kc[0][0].strip(), stream[m2][1][2][0][0].strip()])
                        j = next_line(m2)
                    else:
                        break
                out.append(_md_render_table(rows))
                i = j
                continue
            out.append(text.strip())
            i += 1
            continue

        if _md_is_table_header(text, cells):
            flush_table()
            table = {'header': cells, 'rows': []}
            i += 1
            continue

        # 跨页/无规范表头的接口字段表块（含 Max(..) 长度列的连续字段行）
        if table is None and _md_is_field_row(text, cells):
            field = _md_detect_field_table(stream, i)
            if field is not None:
                fcells, fend = field
                flush_table()
                out.append(_md_render_table(fcells))
                i = fend + 1
                continue

        if _md_is_data_row(cells):
            if table is not None:
                table['rows'].append(cells)
            else:
                out.append(text.strip())
            i += 1
            continue

        # 表格打开时的单单元格续行碎片：并入上一数据行
        if table is not None and table['rows'] and _md_is_fragment(cells):
            ft, fx0, fx1 = cells[0]
            prev = list(table['rows'][-1])
            best_idx, best_d = 0, None
            for idx, (_pt, px0, px1) in enumerate(prev):
                d = min(abs(fx0 - px0), abs(fx0 - px1))
                if best_d is None or d < best_d:
                    best_d, best_idx = d, idx
            ptext, px0, px1 = prev[best_idx]
            joiner = ''
            if ptext and ft:
                pc, nc = ptext.rstrip()[-1], ft.lstrip()[0]
                if not (CJK_RE.match(pc) and CJK_RE.match(nc)):
                    joiner = ' '
            prev[best_idx] = (ptext + joiner + ft, px0, max(px1, fx1))
            table['rows'][-1] = prev
            i += 1
            continue

        flush_table()
        out.append(text.strip())
        i += 1

    flush_table()
    return '\n\n'.join(out)


class OcrEngine:
    def __init__(self):
        self.engine = RapidOCR(use_cls=False)

    def _ocr_image(self, image_data: bytes, min_conf: float = 0.5):
        """对单张图片做 OCR，返回按行排列的 (y, left_x, text)。"""
        pil_img = Image.open(io.BytesIO(image_data)).convert('RGB')
        H0 = pil_img.height
        if H0 > 1400:
            scale = 1400 / H0
            W0 = int(pil_img.width * scale)
            H0 = 1400
            pil_img = pil_img.resize((W0, H0), Image.LANCZOS)
        img = np.array(pil_img)
        H = img.shape[0]
        result, _ = self.engine(img)
        if not result:
            return []

        boxes = []
        for box, text, conf in result:
            try:
                c = float(conf)
            except (TypeError, ValueError):
                c = 0.0
            if c < min_conf:
                continue
            text = text.strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cy = sum(ys) / 4.0
            w = max(xs) - min(xs)
            # 页眉区过滤：顶部窄文本（书名/页码），但保留章节标题
            if cy < 0.12 * H and w < 350 and not HEADING_RE.match(text):
                if _is_header_token(text):
                    continue
            boxes.append((cy, min(xs), text))

        boxes.sort(key=lambda t: (t[0], t[1]))

        rows = []
        for cy, x0, text in boxes:
            if rows and abs(cy - rows[-1][1]) < 36:
                rows[-1][2].append((x0, text))
            else:
                rows.append([cy, cy, [(x0, text)]])
        out = []
        for cy0, _, items in rows:
            items.sort(key=lambda t: t[0])
            text = ''.join(t[1] for t in items)
            left_x = min(t[0] for t in items)
            out.append((cy0, left_x, text))
        return out

    def _render_page_for_ocr(self, pdf_path, page_index):
        """将 PDF 页面渲染为 PNG，作为扫描页图片缺失时的 OCR 兜底。"""
        if fitz is None:
            return None
        try:
            document = fitz.open(pdf_path)
            try:
                page = document.load_page(page_index)
                return page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes('png')
            finally:
                document.close()
        except Exception:
            return None

    def _process_page(self, page, force_ocr=False, pdf_path=None, page_index=None):
        """
        处理单页：可靠文字层优先，否则使用 OCR。
        扫描页优先取内嵌图片；无内嵌图片时才用 fitz 整页渲染（懒加载）。
        """
        raw_text = ''
        if not force_ocr:
            try:
                raw_text = page.extract_text(extraction_mode='layout') or ''
            except Exception:
                try:
                    raw_text = page.extract_text() or ''
                except Exception:
                    raw_text = ''
            raw_text = raw_text.strip()

        if not force_ocr and _text_layer_is_reliable(raw_text):
            return ('text', _clean_text_layer(raw_text))

        imgs = list(page.images)
        if imgs:
            largest = max(imgs, key=lambda img: len(img.data))
            return ('ocr', self._ocr_image(largest.data))
        if pdf_path is not None and page_index is not None:
            rendered = self._render_page_for_ocr(pdf_path, page_index)
            if rendered is not None:
                return ('ocr', self._ocr_image(rendered))
        if raw_text:
            return ('text', _clean_text_layer(raw_text))
        return ('ocr', [])

    def process_pdf(self, pdf_path: str, out_path: str, progress_callback=None):
        reader = PdfReader(pdf_path)
        total = len(reader.pages)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        page_modes = {}
        ocr_payloads = {}
        reliable_text_pages = 0
        for idx, page in enumerate(reader.pages):
            try:
                raw_text = page.extract_text(extraction_mode='layout') or ''
            except Exception:
                try:
                    raw_text = page.extract_text() or ''
                except Exception:
                    raw_text = ''
            if progress_callback:
                progress_callback(idx + 1, total)
            if _text_layer_is_reliable(raw_text):
                page_modes[idx] = 'text'
                reliable_text_pages += 1
                continue

            page_modes[idx] = 'ocr'
            mode, payload = self._process_page(
                page,
                force_ocr=True,
                pdf_path=pdf_path,
                page_index=idx,
            )
            if mode == 'ocr':
                ocr_payloads[idx] = payload

        if reliable_text_pages == total:
            md = convert_text_pdf(reader)
        elif reliable_text_pages == 0:
            # 纯扫描版没有可用文字层，直接使用 OCR 的标题/段落重建，
            # 避免把每个 OCR 行作为普通段落导致前端无法生成目录。
            md = self._convert_ocr_payloads(ocr_payloads, total, progress_callback)
        else:
            # 混合 PDF 按页替换扫描页内容，文字页仍使用坐标重建表格/标题。
            md = convert_text_pdf(reader, ocr_payloads)
            if not md.strip():
                md = self._convert_ocr_payloads(ocr_payloads, total, progress_callback)

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(md)
            if not md.endswith('\n'):
                f.write('\n')
        if progress_callback:
            progress_callback(total, total)

    def _convert_ocr_payloads(self, payloads, total, progress_callback=None):
        """将扫描页 OCR 行按页顺序转换为普通 Markdown。"""
        out = []
        for idx in range(total):
            rows = payloads.get(idx, [])
            if progress_callback:
                progress_callback(idx + 1, total)
            para = []
            for _y, _x, text in rows:
                text = text.strip()
                is_heading = (
                    HEADING_RE.match(text)
                    or OCR_NUMBERED_HEADING_RE.match(text)
                    or (text in NUM_SECTION and len(text) == 1)
                )
                if is_heading and len(text) <= 50:
                    if para:
                        out.append(''.join(para).strip())
                        para = []
                    if text in NUM_SECTION and len(text) == 1:
                        level = 3
                    else:
                        level = 2 if OCR_NUMBERED_HEADING_RE.match(text) else 1
                    out.append('#' * level + ' ' + text)
                else:
                    para.append(text)
            if para:
                out.append(''.join(para).strip())
        return '\n\n'.join(x for x in out if x)
