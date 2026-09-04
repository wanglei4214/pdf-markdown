# PDF OCR → Markdown 网站 MVP 需求文档

## 1. 项目背景与目标

- **背景**：现有本地脚本已能使用 `pypdf + RapidOCR` 将 PDF 内容转换为 Markdown，但使用门槛高、缺少可视化进度与结果管理。
- **目标**：建设一个 SaaS Web 服务，让用户通过浏览器上传扫描版或文字版 PDF，后台自动选择合适的处理方式，最终在线预览、复制和下载 Markdown。
- **MVP 范围**：支持多租户、用户认证、付费订阅和配额管理，支持扫描版 PDF、文字版 PDF，开始产品商业化运营。

## 2. 用户故事

### 2.1 认证与订阅

1. 作为新用户，我可以使用 Google 账号快速注册和登录。
2. 作为免费用户，我可以试用服务并查看我的剩余配额。
3. 作为用户，我可以订阅付费套餐以获得更多处理配额。
4. 作为付费用户，我可以在 Creem 结账页选择 PayPal 或信用卡等方式安全完成支付。
5. 作为用户，我可以在个人中心查看订阅状态、配额使用情况和账单历史。

### 2.2 核心功能

6. 作为用户，我可以在网页上传 PDF，系统告诉我任务已创建。
7. 作为用户，我可以在任务列表看到处理状态、当前页码和总页数。
8. 作为用户，我可以在 PDF 处理完成后在线预览生成的 Markdown。
9. 作为用户，我可以在结果页查看左侧目录，并点击目录跳转到对应章节。
10. 作为用户，我可以下载 `.md` 文件或复制 Markdown 原文。
11. 作为用户，我可以删除已上传的任务和生成的文件，释放磁盘空间。

## 3. 功能需求

### 3.1 用户认证与授权

#### 3.1.1 Google OAuth 登录

- 使用 Google OAuth 2.0 实现第三方登录。
- 用户首次登录自动创建账户,保存 Google ID、邮箱、头像和用户名。
- 支持已有账户通过 Google 登录直接访问。
- 登录后生成 JWT Token,前端保存在 LocalStorage 或 Cookie。
- Token 有效期 7 天,过期后需重新登录。

#### 3.1.2 会话管理

- 所有需要认证的 API 接口检查 JWT Token 有效性。
- Token 无效或过期时返回 401,前端自动跳转登录页。
- 用户可主动退出登录,清除本地 Token。

### 3.2 订阅与付费

#### 3.2.1 套餐设计

定义三个订阅层级:

| 套餐 | 价格 | 月处理页数 | 单文件大小限制 | 保留时长 |
|---|---|---|---|---|
| Free | $0 | 50 页 | 10 MB | 7 天 |
| Pro | $9.99/月 | 1000 页 | 50 MB | 30 天 |
| Enterprise | $29.99/月 | 5000 页 | 200 MB | 90 天 |

- 每个用户注册后自动获得 Free 套餐。
- 页数按自然月重置,未使用配额不累积。
- 保留时长指任务和文件在服务器保存的时间,超期自动删除。

#### 3.2.2 Creem 支付集成（PayPal / 信用卡）

- 采用 [Creem](https://www.creem.io/) 作为 Merchant of Record（记录商户），不单独对接 PayPal API。
- Creem 托管结账页（Hosted Checkout）原生支持 **PayPal**、信用卡（Visa / Mastercard / Amex）、Apple Pay、Google Pay 及各地区本地支付方式；用户在结账页自行选择支付方式。
- 产品套餐（Pro / Enterprise）在 Creem 后台创建，后端通过 Creem API（`https://api.creem.io/v1/checkouts`）创建 Checkout Session 并返回结账页 URL。
- 支付成功后 Creem 通过 Webhook 回调（`checkout.completed`、`subscription.active`、`subscription.canceled`、`subscription.expired` 等事件）更新用户订阅状态和配额；Webhook 使用签名密钥验签。
- 订阅续费、取消、降级通过 Creem 客户门户（Customer Portal）完成。
- 支持测试模式（`creem_test_...` 密钥走沙箱）与生产模式（`creem_live_...`）。
- 订阅到期或取消后自动降级为 Free。
- Creem 作为法定卖方，自动处理 190+ 国家/地区的 VAT/GST/销售税、退款、拒付与欺诈风控。
- 说明：PayPal 仅支持买家付款；商家提现走 Creem 结算（银行转账 / 支付宝 / USDC）。

#### 3.2.3 配额管理

- 用户表增加字段：`subscription_tier`、`monthly_quota`、`used_quota`、`quota_reset_at`、`creem_customer_id`、`creem_subscription_id`。
- 上传 PDF 前检查剩余配额，不足时提示升级。
- 任务完成后根据实际处理页数扣减配额。
- 个人中心显示当前套餐、剩余配额和配额重置时间。

#### 3.2.4 个人中心

- 显示用户头像、用户名和邮箱。
- 显示当前订阅套餐、月配额、已用配额和配额重置时间。
- 提供"升级套餐"按钮，跳转套餐选择页面并创建 Creem Checkout。
- 提供"管理订阅"按钮，跳转 Creem 客户门户（可在其中选择 PayPal / 信用卡、取消或变更订阅）。
- 显示最近的账单记录和付款历史（通过 Creem API 同步）。
- 提供退出登录按钮。

### 3.3 上传模块

- 支持拖拽上传与点击选择。
- 限制文件类型为 `.pdf`。
- 根据用户订阅层级限制单文件大小(Free: 10 MB / Pro: 50 MB / Enterprise: 200 MB)。
- 上传前检查用户剩余配额,配额不足时提示升级。
- 上传成功后立即返回任务 ID，并在任务列表显示任务卡片。
- 上传的文件关联到用户账号,仅该用户可见和管理。

### 3.4 任务管理

- 每个上传对应一个任务，字段包括：
  - 任务 ID（UUID）
  - 用户 ID（关联用户）
  - 文件名、文件大小、上传时间
  - 状态：`pending` / `processing` / `success` / `failed`
  - 当前页码 / 总页码
  - 处理阶段提示（页面分析、OCR、Markdown 转换等）
  - 输出 Markdown 路径
  - 过期时间（根据订阅层级计算）
  - 错误信息（失败时）
- 任务列表仅显示当前用户的任务，按上传时间倒序排列。
- 处理中任务显示当前进度，例如"当前进度 5/441 页"。
- 尚未完成页面分析时显示"正在分析 PDF 页面"。
- 前端定时轮询任务状态，实时刷新进度。
- 支持单个删除；删除任务时清理关联 PDF 和 Markdown 文件。
- 超过保留时长的任务自动标记为过期并清理文件。

### 3.5 PDF 类型识别与处理

系统应兼容以下 PDF 来源：

1. **文字版 PDF**：页面包含可靠的文字层，可直接提取文字和坐标信息。
2. **扫描版 PDF**：页面主要由图片组成，没有可用文字层，需要 OCR。

处理要求：

- 按页判断文字层是否可靠，不能仅依据整份 PDF 的类型判断。
- 支持 PDF 中文字页与扫描页混合的情况。
- 文字层可靠的页面使用 `pypdf` 提取文字和坐标。
- 文字层不可靠的页面进入 OCR 流程。
- 扫描页优先提取 PDF 内嵌图片进行 OCR。
- 页面没有可用内嵌图片时，使用 PyMuPDF 将整页渲染为 PNG，再交给 OCR 引擎识别。
- OCR 失败时保留可提取的残余文字，并记录任务错误信息；不能因单页失败导致服务崩溃。

### 3.6 扫描版 PDF OCR

- 使用 RapidOCR（ONNX Runtime）进行本地 OCR，不依赖外部 OCR API。
- 图像高度超过 1400px 时等比缩放。
- 使用 `use_cls=False` 提升处理速度。
- 识别文字行并按页面顺序合并。
- 尝试去除页眉、页脚和页码等重复内容。
- 根据行首缩进和行间关系重建段落。
- 识别章节编号、`第 X 章`、目录、前言、后记等标题，并转换为 Markdown 标题。
- OCR 生成的 Markdown 标题应供前端生成左侧目录。

### 3.7 文字版 PDF 结构化转换

文字版 PDF 应尽量保留原文语义和版式结构：

- 读取 PDF 书签，并生成对应的 Markdown 目录与章节标题。
- 书签标题与正文标题匹配时避免重复输出；正文缺少标题时在合理位置补充。
- 普通段落按阅读顺序输出，合并同一段落中被 PDF 文字层拆开的内容。
- 识别规范字段表并转换为真正的 Markdown 表格。
- 识别无标准表头的多列对照表，例如地区编码与地区名称表。
- 支持跨行、跨页字段表合并，保持同一字段的列关系。
- 标准字段表列数保持一致；缺少描述等字段时补充空单元格。
- `M`、`C`、`O`、`MS`、`OS`、`CS` 等域类型应归入正确的域类型列。
- 识别返回码表、参数表和其他多列数据表，避免将表格行输出为普通段落。
- 对被分页或文字层拆开的英文字段名、中文字段名和描述进行合理拼接。
- 识别 Java 等代码示例并使用 Markdown 代码块输出。
- 支持跨页代码块连续合并，避免在代码中间插入章节标题或普通文本。
- Markdown 表格单元格中的 `|` 等特殊字符应正确转义。
- 不对无法确认的原文拼写进行大范围自动纠错，避免改变接口文档含义。

### 3.8 结果展示

- 结果页采用左侧目录、右侧正文的布局。
- 左侧目录从 Markdown 标题自动生成，支持至少 `h1`、`h2`、`h3` 层级。
- 点击目录项可跳转到正文对应标题。
- 右侧使用 `marked` 渲染 Markdown。
- 正确展示 Markdown 表格、代码块、列表、标题和段落。
- 表格提供边框、表头背景、单元格间距和横向滚动样式。
- 代码块使用易读的深色背景和等宽字体。
- 提供“复制全文”按钮。
- 提供“下载 .md”按钮。
- 提供返回任务列表按钮。

## 4. 非功能需求

- **自托管**：全部运行在本地或内网服务器，不依赖外部 OCR API。
- **跨平台**：优先支持 Windows，同时兼容 Linux/macOS。
- **易部署**：提供 `requirements.txt` 与启动脚本，理想情况下 `python main.py` 即可运行。
- **数据存储**：任务元数据使用 SQLite，上传文件与输出文件存本地磁盘。
- **并发**：MVP 阶段允许单任务串行处理，后续可扩展为队列。
- **稳定性**：单页解析或 OCR 异常不应导致服务进程崩溃，任务应进入失败状态并保留可诊断信息。

## 5. 技术栈

| 层级 | 选型 | 说明 |
|---|---|---|
| 前端 | HTML + TailwindCSS + 原生 JS | 轻量，无需构建工具 |
| Markdown 渲染 | marked | 支持 GFM 表格和代码块 |
| 后端 | FastAPI | 异步、自动生成 API 文档 |
| 认证 | Google OAuth 2.0 + JWT | 第三方登录和会话管理 |
| 支付 | Creem（Merchant of Record） | 托管结账支持 PayPal / 信用卡 / Apple Pay / Google Pay，含税务、订阅与客户门户 |
| OCR 引擎 | RapidOCR（ONNX Runtime） | 本地 OCR |
| 文字层解析 | pypdf | 提取文字、书签和坐标信息 |
| 页面渲染 | PyMuPDF（pymupdf） | 扫描页无内嵌图片时渲染整页供 OCR |
| 数据库 | PostgreSQL / MySQL | 多租户数据存储 |
| 缓存 | Redis | 会话缓存和任务队列 |
| 任务队列 | Celery | 异步任务处理 |
| 文件存储 | AWS S3 / 阿里云 OSS / 本地磁盘 | 上传文件和输出文件 |
| 部署 | Docker + Kubernetes / AWS ECS | 容器化部署和自动扩展 |

## 6. 系统架构

```
┌─────────────┐                                ┌──────────────┐
│   浏览器    │ ◄─────── HTTPS ────────────► │ FastAPI 后端 │
└─────────────┘                                │ - 认证授权   │
                                               │ - 上传接口   │
        ┌───────────────────────────────────────┤ - 任务 CRUD  │
        │                                       │ - 结果下载   │
        ▼                                       │ - 支付回调   │
 ┌──────────────┐                               └──────┬───────┘
 │ Google OAuth │                                      │
 └──────────────┘                                      │
                                               ┌───────▼────────┐
        ┌──────────────────────────────────────┤ Celery 任务队列│
        │                                      └───────┬────────┘
        ▼                                              │
 ┌──────────────┐                               ┌──────▼──────────┐
 │ Creem 支付   │                               │ Celery Workers  │
 │(PayPal/信用卡)│                               │ - PDF 类型判断  │
 └──────────────┘                               │ - pypdf 提取    │
                                                │ - PyMuPDF 渲染  │
                                                │ - RapidOCR 识别 │
                                                │ - Markdown 输出 │
                                                └──────┬──────────┘
                                                       │
                               ┌───────────────────────┼───────────────┐
                               │                       │               │
                        ┌──────▼─────┐         ┌──────▼──────┐ ┌─────▼─────┐
                        │ PostgreSQL │         │   Redis     │ │  S3 / OSS │
                        │  用户数据  │         │ 任务状态    │ │  文件存储 │
                        │  任务元数据│         │ 会话缓存    │ │           │
                        └────────────┘         └─────────────┘ └───────────┘
```

## 7. 接口草案

### 7.1 认证接口

- `GET /api/auth/google`：跳转 Google OAuth 授权页面。
- `GET /api/auth/google/callback`：Google OAuth 回调，返回 JWT Token。
- `POST /api/auth/logout`：用户退出登录。
- `GET /api/auth/me`：获取当前用户信息。

### 7.2 订阅与支付

- `GET /api/subscription/plans`：获取套餐列表。
- `POST /api/subscription/checkout`：调用 Creem API 创建 Checkout Session 并返回结账页 URL。
- `POST /api/subscription/webhook`：Creem Webhook 回调（验签，处理 checkout/subscription 事件）。
- `GET /api/subscription/portal`：创建并跳转 Creem 客户门户（管理订阅、选择 PayPal / 信用卡）。
- `GET /api/subscription/status`：获取用户订阅和配额信息。

### 7.3 任务接口

- `POST /api/tasks/upload`：上传 PDF，返回任务信息（需认证）。
- `GET /api/tasks`：获取当前用户任务列表、状态和进度（需认证）。
- `GET /api/tasks/{id}`：获取单个任务详情与进度（需认证且属于该用户）。
- `GET /api/tasks/{id}/markdown`：获取 Markdown 内容（需认证且属于该用户）。
- `GET /api/tasks/{id}/download`：下载 `.md` 文件（需认证且属于该用户）。
- `DELETE /api/tasks/{id}`：删除任务及关联文件（需认证且属于该用户）。

## 8. 页面结构

- **登录页**：显示 Google 登录按钮，引导用户授权。
- **首页 / 上传页**：顶部导航（个人中心、配额显示、退出），大文件拖拽区、文件类型提示、最近任务列表入口。
- **任务列表页**：卡片/表格展示文件信息、处理阶段、状态、当前页/总页数和操作按钮。
- **结果页**：文件标题、左侧目录、右侧 Markdown 预览、复制、下载和返回按钮。
- **处理中状态**：显示进度条、当前处理页码和页面分析提示。
- **完成状态**：显示预览、下载、删除等操作。
- **失败状态**：显示失败状态和错误信息，并提供重新处理入口。
- **个人中心页**：用户信息、当前套餐、配额使用、账单历史、升级按钮、管理订阅入口。
- **升级套餐页**：套餐对比、价格展示，点击后跳转 Creem 托管结账页（在结账页选择 PayPal / 信用卡等支付方式）。
- **配额不足提示**：上传前或上传后检测到配额不足时弹窗提示，引导升级。

## 9. 文件目录规划

```
project/
├── main.py                 # FastAPI 入口
├── auth.py                 # Google OAuth 和 JWT 认证
├── subscription.py         # Creem 支付和订阅管理（Checkout / Webhook / 客户门户）
├── ocr_engine.py           # PDF 类型判断、OCR 和 Markdown 转换
├── tasks.py                # Celery 任务定义
├── models.py               # 数据库模型与操作
├── config.py               # 环境变量与配置
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 镜像构建
├── docker-compose.yml      # 服务编排
├── .env.example            # 环境变量模板
├── static/
│   ├── index.html          # 首页
│   ├── login.html          # 登录页
│   ├── profile.html        # 个人中心
│   ├── style.css
│   └── app.js
└── uploads/                # 上传的 PDF (本地开发) / S3 (生产环境)
```

## 10. 部署方式

### 10.1 环境变量配置

创建 `.env` 文件并配置以下变量：

```bash
# 数据库
DATABASE_URL=postgresql://user:password@localhost:5432/pdf_markdown
REDIS_URL=redis://localhost:6379/0

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=https://yourdomain.com/api/auth/google/callback

# JWT
JWT_SECRET=your_jwt_secret_key
JWT_EXPIRE_DAYS=7

# Creem（Merchant of Record，结账页支持 PayPal / 信用卡）
CREEM_API_KEY=creem_test_xxxxx          # 测试: creem_test_ 开头，生产: creem_live_ 开头
CREEM_WEBHOOK_SECRET=whsec_xxxxx
CREEM_STORE_ID=sto_xxxxx
CREEM_WEBHOOK_URL=https://yourdomain.com/api/subscription/webhook

# 文件存储
STORAGE_TYPE=s3  # 或 local
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
S3_BUCKET_NAME=your_bucket_name
S3_REGION=us-east-1

# 应用配置
BASE_URL=https://yourdomain.com
CORS_ORIGINS=https://yourdomain.com
```

### 10.2 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Redis 和 PostgreSQL (使用 Docker)
docker-compose up -d redis postgres

# 启动 FastAPI
python main.py

# 启动 Celery Worker
celery -A tasks worker --loglevel=info

# 访问 http://localhost:8000
```

### 10.3 Docker 部署

```bash
# 构建镜像
docker-compose build

# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 10.4 依赖说明

- `fastapi` 和 `uvicorn` 用于 Web 服务。
- `authlib` 用于 Google OAuth 集成。
- `pyjwt` 用于 JWT Token 生成和验证。
- Creem 通过 REST API 集成（使用 `httpx` 调用），无官方 Python SDK；负责 Checkout、订阅、Webhook 和客户门户，结账页支持 PayPal / 信用卡。
- `celery` 和 `redis` 用于异步任务队列。
- `sqlalchemy` 用于数据库 ORM。
- `pypdf` 用于文字层、坐标和书签提取。
- `rapidocr-onnxruntime` 用于扫描版 OCR。
- `pymupdf` 用于扫描页整页渲染 OCR 兜底。
- `boto3` 用于 AWS S3 文件存储（可选）。

## 11. MVP 不包含（后续迭代）

- 邮箱/手机号注册登录
- 在线编辑 Markdown 并回存
- 团队协作和文件共享
- API 接口对外开放
- 自定义 OCR 模型和参数调整
- 模型热更新 / 多模型切换
- 复杂公式、图片内容和复杂双栏版式的精确还原
- 对无法确认的原文拼写进行自动语义纠错
- 批量上传和批量下载
- Webhook 通知任务完成

## 12. 验收标准

### 12.1 认证与订阅

- [ ] 用户能通过 Google 账号成功注册和登录。
- [ ] 登录后能获得 JWT Token 并访问需要认证的接口。
- [ ] 新用户自动获得 Free 套餐和 50 页配额。
- [ ] 用户能在个人中心查看订阅状态和配额使用情况。
- [ ] 用户能点击升级按钮跳转 Creem Checkout 页面，并能选择 PayPal 或信用卡完成支付。
- [ ] Creem Webhook（checkout.completed / subscription.* 事件）验签通过并正确更新订阅状态和配额。
- [ ] 用户能通过 Creem 客户门户取消或变更订阅，订阅到期后自动降级 Free。
- [ ] 配额不足时上传被阻止并提示升级。
- [ ] 任务完成后配额正确扣减。
- [ ] 超过保留时长的任务自动清理。

### 12.2 核心功能

- [ ] 浏览器能上传扫描版和文字版 PDF 并成功创建任务。
- [ ] 任务列表仅显示当前用户的任务，数据完全隔离。
- [ ] 任务列表能显示处理中、完成、失败等状态。
- [ ] 任务列表能显示页面分析状态和实时当前页/总页数。
- [ ] 扫描版 PDF 能通过 RapidOCR 生成 Markdown；无内嵌图片时能使用整页渲染 OCR 兜底。
- [ ] 文字版 PDF 能提取书签并生成目录和章节标题。
- [ ] 文字版 PDF 中的字段表、对照表、返回码表能转换为 Markdown 表格。
- [ ] 跨行、跨页表格内容能够保持基本列关系，不应大量退化为普通段落。
- [ ] 代码示例能够转换为 Markdown 代码块，并支持跨页连续内容。
- [ ] 结果页左侧能显示 Markdown 标题目录，右侧能正确渲染正文。
- [ ] 浏览器预览能正确显示表格、代码块、列表、标题和段落。
- [ ] 能复制全文和下载 `.md` 文件，且内容完整。
- [ ] 能删除任务并清理文件（本地或 S3）。

### 12.3 部署与稳定性

- [ ] 服务能通过 Docker Compose 一键启动。
- [ ] Celery Worker 能并行处理多个任务。
- [ ] 单页 OCR 失败不导致整个任务失败或服务崩溃。
- [ ] Creem Webhook 能正确处理订阅事件且验签失败时拒绝请求。
- [ ] 所有需要认证的接口正确验证 JWT Token。
