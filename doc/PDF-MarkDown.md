# PDF-MarkDown 项目功能说明

## 1. 项目概述

本项目是一个本地/内网使用的 PDF 转 Markdown Web 工具，支持两类 PDF：

- **扫描版 PDF**：页面通常没有可提取文字，使用 RapidOCR 识别页面内容。
- **文字版 PDF**：页面包含文字层，优先使用 pypdf 提取文字，并保留 PDF 中的书签、章节、表格和代码结构。
- **混合型 PDF**：按页判断文字层质量，文字页和扫描页分别处理后按原页序合并。

系统由 FastAPI 后端、OCR/文字转换引擎、SQLite 任务管理和原生 HTML/JavaScript 前端组成。

## 2. 目录结构

```text
betrayal/
├── doc/
│   ├── MVP-需求文档.md
│   └── PDF-MarkDown.md
├── web/
│   ├── main.py                 # FastAPI 应用和任务接口
│   ├── models.py               # SQLite 任务数据操作
│   ├── ocr_engine.py           # PDF 识别与 Markdown 转换核心
│   ├── requirements.txt        # Python 依赖
│   ├── start.ps1               # Windows 启动脚本
│   ├── static/
│   │   ├── index.html          # 页面结构
│   │   ├── app.js              # 前端交互、轮询和预览
│   │   └── style.css           # 页面及 Markdown 样式
│   ├── uploads/                # 上传的 PDF 文件
│   ├── outputs/                # 生成的 Markdown 文件
│   └── tasks.db                # SQLite 数据库
└── .pylibs310/                 # 当前环境使用的 Python 依赖目录
```

## 3. 后端功能

### 3.1 应用入口

文件：[web/main.py](../web/main.py)

- 使用 FastAPI 创建 Web 服务。
- 挂载 `/static` 静态文件目录。
- 根路径 `/` 重定向到 `/static/index.html`。
- 上传成功后启动后台线程执行 PDF 转换。
- 服务重启时，将遗留的 `processing` 任务标记为失败，用户可以重新处理。

### 3.2 API 接口

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/api/tasks/upload` | 上传 PDF，创建任务并启动后台处理 |
| GET | `/api/tasks` | 获取任务列表，按创建时间倒序 |
| GET | `/api/tasks/{id}` | 获取任务详情和处理进度 |
| GET | `/api/tasks/{id}/markdown` | 获取 Markdown 原文 |
| GET | `/api/tasks/{id}/download` | 下载 Markdown 文件 |
| POST | `/api/tasks/{id}/retry` | 重新处理失败或中断的任务 |
| DELETE | `/api/tasks/{id}` | 删除任务、PDF 和 Markdown 文件 |

### 3.3 任务状态

任务记录保存在 [web/models.py](../web/models.py) 管理的 SQLite 数据库中。

- `pending`：任务已创建，等待处理。
- `processing`：后台线程正在处理。
- `success`：转换完成，可以预览和下载。
- `failed`：处理失败，记录错误信息，可以重新处理。

进度字段：

- `current_page`：当前处理页码。
- `total_pages`：PDF 总页数。
- `output_path`：生成的 Markdown 文件路径。
- `error_message`：失败时的异常信息。

## 4. PDF 处理流程

核心文件：[web/ocr_engine.py](../web/ocr_engine.py)

```text
打开 PDF
  ↓
逐页提取文字层
  ↓
判断当前页文字层是否可靠
  ├─ 可靠文字页 → 文字层转换
  └─ 不可靠/无文字页 → 图片 OCR
                         ├─ PDF 内嵌图片
                         └─ PyMuPDF 整页渲染 PNG 兜底
  ↓
按页面模式合并
  ↓
生成 Markdown 文件
```

### 4.1 文字层可靠性判断

`_text_layer_is_reliable()` 会对每页文字层进行基础质量判断，包括：

- 提取文字数量是否达到最低阈值。
- 是否存在大量连续重复中文字符。
- 是否存在过多异常短行。
- 是否属于明显不可靠的文字层。

可靠文字页进入文字版转换流程；不可靠页面进入 OCR 流程。因此不根据整份 PDF 统一判断，而是支持同一个 PDF 内部混合页面。

### 4.2 扫描版 OCR

扫描页的处理顺序如下：

1. 优先获取 PDF 内嵌图片。
2. 选择页面中最大的图片进行 OCR。
3. 如果没有可用内嵌图片，使用 PyMuPDF 将页面按 2 倍比例渲染成 PNG。
4. 将渲染图片交给 RapidOCR。
5. OCR 行按页面顺序转换为 Markdown 段落和标题。

PyMuPDF 的导入是可选的：

```python
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None
```

文字版 PDF 不依赖 PyMuPDF；扫描版 PDF 在没有标准内嵌图片时依赖其整页渲染兜底能力。

### 4.3 扫描版目录生成

OCR 结果会识别以下标题形式：

- `第X章`、`第X节` 等中文章节标题。
- `目录`、`前言`、`序言`、`后记`、`引子`、`楔子` 等固定标题。
- `1. 标题`、`1.1 标题`、`2、标题` 等数字编号标题。
- 单独的“一”“二”等中文编号。

识别到的标题会转换为 Markdown 标题，前端据此生成左侧目录。

## 5. 文字版 PDF 语义化转换

文字版 PDF 由 pypdf 提取文字及坐标，核心目标是尽量还原原 PDF 的文档结构。

### 5.1 书签和章节

- 读取 PDF outline/bookmark。
- 获取书签对应页码。
- 在正文中匹配对应标题。
- 匹配成功时输出 Markdown 标题。
- 书签指向目录页或正文位置不准确时，支持在目标页注入标题。
- 对目录条目和重复标题进行抑制，避免生成重复章节。

### 5.2 坐标行重建

文字层使用页面坐标重建阅读顺序：

- 按 y 坐标聚合为文本行。
- 按 x 坐标排序行内单元格。
- 通过相邻文本的水平间距判断列分隔。
- 结合列锚点处理跨行和跨页字段。

### 5.3 表格识别

当前支持以下常见表格类型：

1. 标准字段表：根据多个列单元格和表头识别。
2. 三列小表：用于较简单的字段说明表。
3. 网格对照表：识别地区编码、地区名称等多列对照关系。
4. 跨页字段表：识别无明显表头但具有 `Max()`、域类型等特征的字段行。
5. 返回码表：识别数字返回码和中文说明。

表格输出为真正的 Markdown GFM 表格：

```markdown
| 地区编码 | 地区名称 |
| --- | --- |
| 100 | 北京 |
| 1200 | 天津 |
```

字段表会尽量保持固定列数；当最后一列为 `M`、`C`、`O`、`MS`、`OS`、`CS` 等域类型时，会归入域类型列，避免与描述列错位。

### 5.4 表格异常处理

文字版 PDF 可能将一行内容拆成多行或多个文本片段，转换器会尝试：

- 合并相邻行的英文字段碎片。
- 将 `Max()`、域类型和字段名归并到同一表格行。
- 根据 x 坐标将单元格映射到最近列锚点。
- 跨页继续识别同一类字段表。
- 排除把字段表误识别为普通网格表的情况。
- 对 Markdown 表格单元格中的 `|` 进行转义。

PDF 文字层本身存在的拼写错误、缺字或字符顺序错误，不应无条件自动纠正；优先修复确定的版面拆分和列错位问题。

### 5.5 代码块

代码示例会根据 Java、JSON 等代码特征转换为 Markdown 代码块，并支持分页后的代码行继续拼接，避免在代码块中间插入普通标题。

## 6. 前端展示功能

前端文件位于 [web/static](../web/static)。

### 6.1 上传页

- 支持点击选择 PDF。
- 支持拖拽上传。
- 页面明确提示支持扫描版和文字版 PDF。
- 展示扫描版 OCR、文字层解析、表格与目录保留等能力。
- 上传后自动进入任务列表。

### 6.2 任务列表

- 显示原始文件名、文件大小和创建时间。
- 显示等待中、处理中、完成、失败状态。
- 处理中显示当前页码/总页数和进度条。
- 尚未获取总页数时显示页面分析状态。
- 完成任务提供预览和下载。
- 失败或中断任务提供继续处理/重试。
- 所有任务提供删除操作。
- 对处理中和等待中的任务每 2 秒自动轮询。

### 6.3 Markdown 结果页

- 左侧显示 Markdown 标题目录。
- 右侧显示 Markdown 渲染后的正文。
- 支持表格、代码块、列表和标题样式。
- 支持复制全文和下载 Markdown。
- 右侧正文为独立滚动区域。
- 左侧目录为独立滚动区域。
- 正文滚动时自动高亮当前章节，并将当前目录项滚动到可见位置。
- 点击目录项时，右侧正文准确滚动到对应标题。

目录同步逻辑位于 [web/static/app.js](../web/static/app.js) 的 `buildToc()`，使用正文容器坐标而不是浏览器页面坐标计算标题位置。

## 7. 前端样式注意事项

文件：[web/static/style.css](../web/static/style.css)

项目使用 Tailwind CDN。Tailwind preflight 会重置原生表格样式，因此需要显式设置：

- 表格边框和边框合并。
- 表头背景色和字体粗细。
- 单元格内边距和垂直对齐。
- 超宽表格的横向滚动。
- 代码块背景、字体和换行。
- 目录当前项高亮样式。
- 目录和正文的独立高度与滚动行为。

## 8. Python 依赖

依赖文件：[web/requirements.txt](../web/requirements.txt)

| 依赖 | 用途 |
|---|---|
| FastAPI | Web API 服务 |
| Uvicorn | ASGI 服务启动 |
| python-multipart | 文件上传解析 |
| pypdf | PDF 文字层、页面和书签读取 |
| Pillow | 图片读取和处理 |
| numpy | OCR 图像数据处理 |
| rapidocr-onnxruntime | 本地 OCR 识别 |
| pymupdf | 扫描页整页渲染 OCR 兜底 |

## 9. 启动方式

Windows 当前启动脚本为 [web/start.ps1](../web/start.ps1)：

```powershell
cd c:\Users\wangl\Documents\trae\betrayal\web
.\start.ps1
```

默认访问地址：

```text
http://127.0.0.1:8000
```

通用安装方式：

```powershell
pip install -r web/requirements.txt
python web/main.py
```

当前脚本使用 Python 3.10，并通过 `PYTHONPATH` 加载项目的 `.pylibs310` 和 `web` 目录。更换环境时应确认 pypdf、Pillow、RapidOCR、PyMuPDF 与 Python 版本兼容。

## 10. 问题定位指南

### 10.1 文字版 PDF 表格缺失

优先检查 [web/ocr_engine.py](../web/ocr_engine.py)：

- 页面是否被判断为可靠文字页。
- `_md_detect_grid_table()` 是否识别到对照表。
- `_md_detect_field_table()` 是否识别到跨页字段表。
- 表格列锚点是否因 PDF 坐标异常而错位。
- 输出 Markdown 是否包含完整的 `|` 表格行。

### 10.2 扫描版 PDF 没有内容

检查：

- RapidOCR 是否安装并能初始化。
- PDF 是否包含内嵌图片。
- PyMuPDF 是否安装成功。
- `_render_page_for_ocr()` 是否能渲染页面。
- OCR 结果是否为空或被置信度过滤。

### 10.3 左侧目录为空

检查：

- Markdown 是否实际包含 `#`、`##` 或 `###` 标题。
- 扫描版 OCR 标题识别规则是否命中。
- 前端是否成功执行 `marked.parse()`。
- `buildToc()` 是否在结果区域显示后执行。

### 10.4 目录与正文无法对齐

检查：

- `#result-content` 是否是独立滚动容器。
- `buildToc()` 是否使用正文容器坐标计算标题位置。
- 正文滚动事件是否绑定到 `#result-content`。
- 浏览器是否加载了最新的 `app.js` 和 `style.css`。

修改前端后建议使用 `Ctrl + F5` 强制刷新。

## 11. 当前边界

- OCR 表格的精确列还原能力弱于文字版 PDF，因为 OCR 结果目前主要依赖行文本，不完整使用表格坐标。
- PDF 原始文字层存在的错字、缺字和复杂双栏排版仍可能影响结果。
- 任务执行采用后台线程，当前 MVP 适合单用户或低并发使用。
- 服务重启不会恢复正在执行的线程，相关任务会被标记为失败并需要重试。
- 当前未实现用户登录、多租户、在线编辑回存和任务队列。

## 12. 修改代码时的原则

1. 优先按页面处理，不要用整份 PDF 的单一模式覆盖混合 PDF。
2. 文字版 PDF 优先保留原始结构，不要直接把所有文字拼成普通段落。
3. 表格识别需要同时参考文本内容、单元格数量和 x/y 坐标。
4. 扫描页没有图片时，使用 PyMuPDF 整页渲染作为 OCR 兜底。
5. 前端目录必须与实际 Markdown 标题对应，滚动计算使用正文滚动容器坐标。
6. 不要直接修改或覆盖 `uploads/`、`outputs/` 中的用户文件，除非用户明确要求重新处理。
7. 修改后优先执行 Python 编译检查、JavaScript 语法检查和针对性转换验证。
