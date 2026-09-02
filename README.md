# PDF to Markdown Web 服务

基于 FastAPI + RapidOCR + pypdf 的 PDF 转 Markdown 本地 Web 工具，支持三类 PDF：

- **扫描版 PDF**：页面无可提取文字，使用 RapidOCR 识别（无内嵌图片时用 PyMuPDF 整页渲染兜底）。
- **文字版 PDF**：使用 pypdf 提取文字层，保留书签章节、GFM 表格和代码块结构。
- **混合型 PDF**：按页判断文字层质量，文字页走文字层、扫描页走 OCR，最后按原页序合并。

## 环境要求

- Python 3.10（也可使用兼容的 3.11/3.12，需确认依赖可正常安装）
- 依赖通过 `requirements.txt` 安装：FastAPI、uvicorn、pypdf、Pillow、numpy、rapidocr-onnxruntime、pymupdf

## 启动方式

### 方式一：Docker Compose（推荐）

```powershell
docker compose up -d --build
```

启动后访问：http://localhost （nginx 监听 80 端口，反代 `/api/` 到后端，静态页面由 nginx 直接托管）

任务数据库和生成的文件通过卷映射持久化到宿主机的 `tasks.db`、`uploads/`、`outputs/`。

### 方式二：Windows PowerShell

```powershell
.\start.ps1
```

脚本会自动切换到项目目录，并使用项目内的 `.venv310` 虚拟环境（Python 3.10）启动 uvicorn。首次使用需先创建虚拟环境：

```powershell
py -3.10 -m venv .venv310
.\.venv310\Scripts\python.exe -m pip install -r requirements.txt
```

> 注意：`rapidocr-onnxruntime` 要求 Python < 3.13，因此必须使用 Python 3.10/3.11/3.12，不能使用 Python 3.13+。

### 方式三：手动启动

```powershell
py -3.10 -m venv .venv310
.\.venv310\Scripts\python.exe -m pip install -r requirements.txt
.\.venv310\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

直接运行后端时访问：http://127.0.0.1:8000

> 数据目录可通过环境变量 `DATA_DIR`（默认项目根目录，存放 `uploads/`、`outputs/`）和 `DB_PATH`（默认 `tasks.db`）覆盖，Docker 部署即通过这两个变量将数据指向 `/app/data`。

## 功能

1. 上传 PDF（拖拽或点击选择），后台线程处理并显示实时页码进度
2. 扫描页 OCR 识别，自动识别章节标题并生成 Markdown 目录
3. 文字页保留书签章节、表格（字段表、对照表、返回码表等）和代码块
4. 完成后在线预览：左侧标题目录与右侧正文滚动联动，支持复制全文、下载 `.md`
5. 失败或中断（如服务重启）的任务可一键重试
6. 支持删除任务及生成的文件

## 目录说明

- `main.py`：FastAPI 后端入口与任务 API
- `models.py`：SQLite 任务模型
- `ocr_engine.py`：OCR 与文字层转 Markdown 核心逻辑
- `static/`：前端页面（index.html、app.js、style.css）
- `uploads/`：上传的 PDF（运行时自动创建）
- `outputs/`：生成的 Markdown（运行时自动创建）
- `tasks.db`：SQLite 数据库（运行时自动创建）
- `Dockerfile` / `docker-compose.yml` / `nginx.conf`：Docker 部署配置
- `doc/`：功能说明与需求文档
- `requirements.txt`：Python 依赖
- `start.ps1`：Windows 本地启动脚本
