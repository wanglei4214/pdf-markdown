# PDF OCR → Markdown SaaS MVP Requirements

## 1. Background and Objectives

- **Background**: Existing local scripts can convert PDF content to Markdown using `pypdf + RapidOCR`, but they have a high barrier to entry and lack visual progress and result management.
- **Objective**: Build a SaaS web service that allows users to upload scanned or text-based PDFs via a browser, automatically selects appropriate processing methods, and provides online preview, copy, and download of Markdown.
- **MVP Scope**: Support multi-tenancy, user authentication, paid subscriptions, and quota management. Support scanned PDFs and text-based PDFs to begin product commercialization.

## 2. User Stories

### 2.1 Authentication and Subscription

1. As a new user, I can quickly register and log in using my Google account.
2. As a free user, I can try the service and view my remaining quota.
3. As a user, I can subscribe to paid plans to get more processing quota.
4. As a paid user, I can securely pay with PayPal, credit card, or other methods on the Creem checkout page.
5. As a user, I can view my subscription status, quota usage, and billing history in my profile.

### 2.2 Core Features

6. As a user, I can upload a PDF on the webpage and the system tells me a task has been created.
7. As a user, I can see processing status, current page number, and total pages in the task list.
8. As a user, I can preview the generated Markdown online after PDF processing is complete.
9. As a user, I can view a table of contents on the left side of the result page and click to jump to corresponding sections.
10. As a user, I can download `.md` files or copy Markdown source.
11. As a user, I can delete uploaded tasks and generated files to free up disk space.

## 3. Functional Requirements

### 3.1 User Authentication and Authorization

#### 3.1.1 Google OAuth Login

- Use Google OAuth 2.0 for third-party login.
- Automatically create an account on first login, saving Google ID, email, avatar, and username.
- Support existing accounts to directly access via Google login.
- Generate JWT Token after login, stored in LocalStorage or Cookie on the frontend.
- Token validity period of 7 days, requiring re-login after expiration.

#### 3.1.2 Session Management

- All authenticated API endpoints check JWT Token validity.
- Return 401 when token is invalid or expired, frontend automatically redirects to login page.
- Users can actively log out to clear local token.

### 3.2 Subscription and Payment

#### 3.2.1 Plan Design

Define three subscription tiers:

| Plan | Price | Monthly Page Quota | File Size Limit | Retention Period |
|---|---|---|---|---|
| Free | $0 | 50 pages | 10 MB | 7 days |
| Pro | $9.99/month | 1000 pages | 50 MB | 30 days |
| Enterprise | $29.99/month | 5000 pages | 200 MB | 90 days |

- Each user automatically receives the Free plan upon registration.
- Page quotas reset monthly; unused quota does not carry over.
- Retention period refers to the time tasks and files are kept on the server before automatic deletion.

#### 3.2.2 Creem Payment Integration (PayPal / Credit Card)

- Use [Creem](https://www.creem.io/) as the Merchant of Record (MoR); do not integrate the PayPal API directly.
- Creem's hosted checkout natively supports **PayPal**, credit cards (Visa / Mastercard / Amex), Apple Pay, Google Pay, and regional local payment methods; users choose their payment method on the checkout page.
- Plans (Pro / Enterprise) are created in the Creem dashboard; the backend calls the Creem API (`https://api.creem.io/v1/checkouts`) to create a Checkout Session and returns the checkout URL.
- After successful payment, Creem sends Webhook events (`checkout.completed`, `subscription.active`, `subscription.canceled`, `subscription.expired`, etc.) to update subscription status and quota; Webhooks are verified using a signing secret.
- Subscription renewal, cancellation, and downgrade are handled through Creem's Customer Portal.
- Support test mode (`creem_test_...` keys use the sandbox) and live mode (`creem_live_...`).
- Automatically downgrade to Free when a subscription expires or is canceled.
- As the legal seller of record, Creem handles VAT/GST/sales tax, refunds, chargebacks, and fraud risk in 190+ countries.
- Note: PayPal is supported for buyer payments only; merchant payouts are settled by Creem via bank transfer, Alipay, or USDC.

#### 3.2.3 Quota Management

- User table includes fields: `subscription_tier`, `monthly_quota`, `used_quota`, `quota_reset_at`, `creem_customer_id`, `creem_subscription_id`.
- Check remaining quota before uploading PDF; prompt to upgrade if insufficient.
- Deduct quota based on actual processed pages after task completion.
- Profile displays current plan, remaining quota, and quota reset time.

### 3.3 Upload Module

- Support drag-and-drop upload and click-to-select.
- Restrict file type to `.pdf`.
- Limit file size based on user subscription tier (Free: 10 MB / Pro: 50 MB / Enterprise: 200 MB).
- Check user's remaining quota before upload; prompt to upgrade if insufficient.
- Return task ID immediately after successful upload and display task card in task list.
- Uploaded files are associated with user account and only visible and manageable by that user.

### 3.4 Task Management

- Each upload corresponds to a task with the following fields:
  - Task ID (UUID)
  - User ID (associated user)
  - Filename, file size, upload time
  - Status: `pending` / `processing` / `success` / `failed`
  - Current page / total pages
  - Processing stage hints (page analysis, OCR, Markdown conversion, etc.)
  - Output Markdown path
  - Expiration time (calculated based on subscription tier)
  - Error message (on failure)
- Task list only displays current user's tasks, sorted by upload time in descending order.
- Processing tasks display current progress, e.g., "Progress 5/441 pages".
- Display "Analyzing PDF pages" when page analysis is not yet complete.
- Frontend polls task status at regular intervals for real-time progress updates.
- Support individual deletion; delete associated PDF and Markdown files when deleting tasks.
- Tasks exceeding retention period are automatically marked as expired and files are cleaned up.

### 3.5 PDF Type Recognition and Processing

The system should be compatible with the following PDF sources:

1. **Text-based PDF**: Pages contain reliable text layers from which text and coordinate information can be directly extracted.
2. **Scanned PDF**: Pages consist mainly of images with no usable text layer, requiring OCR.

Processing requirements:

- Judge text layer reliability page by page; do not judge based solely on entire PDF type.
- Support PDFs with mixed text pages and scanned pages.
- Use `pypdf` to extract text and coordinates for pages with reliable text layers.
- Pages with unreliable text layers enter OCR workflow.
- Prioritize extracting embedded images from scanned pages for OCR.
- When pages have no usable embedded images, use PyMuPDF to render entire page as PNG and submit to OCR engine.
- Retain extractable residual text and record task error information when OCR fails; single page failures should not crash the service.

### 3.6 Scanned PDF OCR

- Use RapidOCR (ONNX Runtime) for local OCR without depending on external OCR APIs.
- Scale images proportionally when height exceeds 1400px.
- Use `use_cls=False` to improve processing speed.
- Recognize text lines and merge them in page order.
- Attempt to remove repeated content like headers, footers, and page numbers.
- Reconstruct paragraphs based on line indentation and inter-line relationships.
- Recognize chapter numbers, "Chapter X", table of contents, prefaces, postscripts, and other headings, converting them to Markdown headings.
- OCR-generated Markdown headings should be used by frontend to generate left sidebar table of contents.

### 3.7 Text-based PDF Structured Conversion

Text-based PDFs should preserve original semantics and layout structure as much as possible:

- Read PDF bookmarks and generate corresponding Markdown table of contents and chapter headings.
- Avoid duplicate output when bookmark titles match body titles; supplement at appropriate positions when body lacks titles.
- Output normal paragraphs in reading order, merging content split by PDF text layer within the same paragraph.
- Recognize standard field tables and convert to proper Markdown tables.
- Recognize multi-column comparison tables without standard headers, e.g., region code and name tables.
- Support cross-row and cross-page field table merging while maintaining column relationships for the same field.
- Standard field tables maintain consistent column count; supplement empty cells when description fields are missing.
- Domain types like `M`, `C`, `O`, `MS`, `OS`, `CS` should be included in the correct domain type column.
- Recognize response code tables, parameter tables, and other multi-column data tables; avoid outputting table rows as normal paragraphs.
- Reasonably concatenate English field names, Chinese field names, and descriptions split by pagination or text layer.
- Recognize Java and other code examples and output using Markdown code blocks.
- Support continuous merging of cross-page code blocks; avoid inserting chapter headings or plain text in the middle of code.
- Special characters like `|` in Markdown table cells should be properly escaped.
- Do not perform large-scale automatic spell correction on unconfirmed original spelling to avoid changing API documentation meaning.

### 3.8 Result Display

- Result page adopts layout with table of contents on the left and body text on the right.
- Left sidebar table of contents automatically generated from Markdown headings, supporting at least `h1`, `h2`, `h3` levels.
- Clicking table of contents items jumps to corresponding heading in body text.
- Right side uses `marked` to render Markdown.
- Correctly display Markdown tables, code blocks, lists, headings, and paragraphs.
- Tables provide borders, header background, cell spacing, and horizontal scrolling styles.
- Code blocks use readable dark background and monospace font.
- Provide "Copy All" button.
- Provide "Download .md" button.
- Provide return to task list button.

### 3.9 Profile Center

- Display user avatar, username, and email.
- Display current subscription plan, monthly quota, used quota, and quota reset time.
- Provide "Upgrade Plan" button to open plan selection and create a Creem Checkout.
- Provide "Manage Subscription" button redirecting to Creem's Customer Portal (where users can choose PayPal / credit card, cancel, or change subscription).
- Billing records and payment history are synced via the Creem API.
- Provide logout button.

## 4. Non-Functional Requirements

- **Multi-tenancy**: Complete data isolation; users can only access their own tasks and files.
- **Security**: All API endpoints require authentication; sensitive operations use HTTPS; payment information is hosted and processed by Creem (Merchant of Record).
- **Cross-platform**: Prioritize cloud deployment (Linux) while compatible with local development environments (Windows/macOS).
- **Easy Deployment**: Provide `requirements.txt`, environment variable configuration, and startup scripts; support Docker deployment.
- **Data Storage**: User and task metadata use PostgreSQL or MySQL; uploaded and output files stored in object storage (AWS S3 / Alibaba Cloud OSS) or local disk.
- **Concurrency**: Use Celery + Redis for task queue, supporting multiple workers for parallel processing.
- **Stability**: Single page parsing or OCR exceptions should not crash service process; tasks should enter failed state and retain diagnostic information.
- **Scalability**: Support horizontal scaling by adding worker nodes to improve processing capacity.
- **Monitoring**: Record user behavior, API calls, task success rate, and payment conversion rate.

## 5. Technology Stack

| Layer | Choice | Description |
|---|---|---|
| Frontend | HTML + TailwindCSS + Vanilla JS | Lightweight, no build tools needed |
| Markdown Rendering | marked | Supports GFM tables and code blocks |
| Backend | FastAPI | Async, auto-generated API docs |
| Authentication | Google OAuth 2.0 + JWT | Third-party login and session management |
| Payment | Creem (Merchant of Record) | Hosted checkout with PayPal / Credit Card / Apple Pay / Google Pay; includes tax, subscriptions, and customer portal |
| OCR Engine | RapidOCR (ONNX Runtime) | Local OCR |
| Text Layer Parsing | pypdf | Extract text, bookmarks, and coordinate info |
| Page Rendering | PyMuPDF (pymupdf) | Render entire scanned page for OCR when no embedded images |
| Database | PostgreSQL / MySQL | Multi-tenant data storage |
| Cache | Redis | Session cache and task queue |
| Task Queue | Celery | Asynchronous task processing |
| File Storage | AWS S3 / Alibaba Cloud OSS / Local | Uploaded and output files |
| Deployment | Docker + Kubernetes / AWS ECS | Containerized deployment and auto-scaling |

## 6. System Architecture

```
┌─────────────┐                                ┌──────────────┐
│   Browser   │ ◄─────── HTTPS ────────────► │ FastAPI      │
└─────────────┘                                │ - Auth       │
                                               │ - Upload     │
       ┌───────────────────────────────────────┤ - Task CRUD  │
       │                                       │ - Download   │
       ▼                                       │ - Payment CB │
┌──────────────┐                               └──────┬───────┘
│ Google OAuth │                                      │
└──────────────┘                                      │
                                              ┌───────▼────────┐
       ┌──────────────────────────────────────┤ Celery Queue   │
       │                                      └───────┬────────┘
       ▼                                              │
┌──────────────┐                               ┌──────▼──────────┐
│ Creem Pay    │                               │ Celery Workers  │
│(PayPal/Card) │                               │ - PDF type      │
└──────────────┘                               │ - pypdf extract │
                                               │ - PyMuPDF render│
                                               │ - RapidOCR      │
                                               │ - MD output     │
                                               └──────┬──────────┘
                                                      │
                              ┌───────────────────────┼───────────────┐
                              │                       │               │
                       ┌──────▼─────┐         ┌──────▼──────┐ ┌─────▼─────┐
                       │ PostgreSQL │         │   Redis     │ │  S3 / OSS │
                       │  User data │         │ Task status │ │  Storage  │
                       │  Task meta │         │ Session     │ │           │
                       └────────────┘         └─────────────┘ └───────────┘
```

## 7. API Endpoints

### 7.1 Authentication

- `GET /api/auth/google`: Redirect to Google OAuth authorization page.
- `GET /api/auth/google/callback`: Google OAuth callback, returns JWT Token.
- `POST /api/auth/logout`: User logout.
- `GET /api/auth/me`: Get current user information.

### 7.2 Subscription and Payment

- `GET /api/subscription/plans`: Get list of plans.
- `POST /api/subscription/checkout`: Create a Creem Checkout Session and return the checkout URL.
- `POST /api/subscription/webhook`: Creem Webhook callback (signature verification; handle checkout/subscription events).
- `GET /api/subscription/portal`: Create and redirect to the Creem Customer Portal (manage subscription, choose PayPal / credit card).
- `GET /api/subscription/status`: Get user subscription and quota information.

### 7.3 Tasks

- `POST /api/tasks/upload`: Upload PDF, return task info (requires auth).
- `GET /api/tasks`: Get current user's task list, status, and progress (requires auth).
- `GET /api/tasks/{id}`: Get single task details and progress (requires auth and ownership).
- `GET /api/tasks/{id}/markdown`: Get Markdown content (requires auth and ownership).
- `GET /api/tasks/{id}/download`: Download `.md` file (requires auth and ownership).
- `DELETE /api/tasks/{id}`: Delete task and associated files (requires auth and ownership).

## 8. Page Structure

- **Login Page**: Display Google login button, guide user authorization.
- **Home / Upload Page**: Top navigation (profile, quota display, logout), large file drag area, file type hints, recent tasks entry.
- **Task List Page**: Cards/table displaying file info, processing stage, status, current/total pages, and action buttons.
- **Result Page**: File title, left sidebar table of contents, right Markdown preview, copy, download, and return buttons.
- **Processing Status**: Display progress bar, current processing page, and page analysis hints.
- **Completed Status**: Display preview, download, delete actions.
- **Failed Status**: Display failure status and error message, provide retry entry.
- **Profile Page**: User info, current plan, quota usage, billing history, upgrade button.
- **Quota Insufficient Prompt**: Popup prompt before or after upload when quota insufficient, guide to upgrade.

## 9. Directory Structure

```
project/
├── main.py                 # FastAPI entry point
├── auth.py                 # Google OAuth and JWT authentication
├── subscription.py         # Creem payment and subscription (Checkout / Webhook / Customer Portal)
├── ocr_engine.py           # PDF type detection, OCR, and Markdown conversion
├── tasks.py                # Celery task definitions
├── models.py               # Database models and operations
├── config.py               # Environment variables and configuration
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image build
├── docker-compose.yml      # Service orchestration
├── .env.example            # Environment variable template
├── static/
│   ├── index.html          # Home page
│   ├── login.html          # Login page
│   ├── profile.html        # Profile page
│   ├── style.css
│   └── app.js
└── uploads/                # Uploaded PDFs (local dev) / S3 (production)
```

## 10. Deployment

### 10.1 Environment Configuration

Create `.env` file and configure the following variables:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/pdf_markdown
REDIS_URL=redis://localhost:6379/0

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=https://yourdomain.com/api/auth/google/callback

# JWT
JWT_SECRET=your_jwt_secret_key
JWT_EXPIRE_DAYS=7

# Creem (Merchant of Record; checkout supports PayPal / credit card)
CREEM_API_KEY=creem_test_xxxxx          # test: prefixed with creem_test_, live: creem_live_
CREEM_WEBHOOK_SECRET=whsec_xxxxx
CREEM_STORE_ID=sto_xxxxx
CREEM_WEBHOOK_URL=https://yourdomain.com/api/subscription/webhook

# File Storage
STORAGE_TYPE=s3  # or local
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
S3_BUCKET_NAME=your_bucket_name
S3_REGION=us-east-1

# Application Config
BASE_URL=https://yourdomain.com
CORS_ORIGINS=https://yourdomain.com
```

### 10.2 Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start Redis and PostgreSQL (using Docker)
docker-compose up -d redis postgres

# Start FastAPI
python main.py

# Start Celery Worker
celery -A tasks worker --loglevel=info

# Visit http://localhost:8000
```

### 10.3 Docker Deployment

```bash
# Build images
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f
```

### 10.4 Dependencies

- `fastapi` and `uvicorn` for web service.
- `authlib` for Google OAuth integration.
- `pyjwt` for JWT token generation and verification.
- Creem is integrated via its REST API (called with `httpx`); there is no official Python SDK. It handles Checkout, subscriptions, Webhooks, and the Customer Portal; the checkout page supports PayPal / credit card.
- `celery` and `redis` for asynchronous task queue.
- `sqlalchemy` for database ORM.
- `pypdf` for text layer, coordinate, and bookmark extraction.
- `rapidocr-onnxruntime` for scanned PDF OCR.
- `pymupdf` for rendering entire scanned page as OCR fallback.
- `boto3` for AWS S3 file storage (optional).

## 11. Not Included in MVP (Future Iterations)

- Email/phone registration and login
- Online Markdown editing and saving
- Team collaboration and file sharing
- Public API access
- Custom OCR models and parameter tuning
- Model hot-swapping / multi-model switching
- Complex formulas, image content, and complex multi-column layout accurate restoration
- Automatic semantic spell correction for unconfirmed original spelling
- Batch upload and download
- Webhook notifications for task completion

## 12. Acceptance Criteria

### 12.1 Authentication and Subscription

- [ ] Users can successfully register and log in with Google account.
- [ ] After login, users receive JWT Token and can access authenticated endpoints.
- [ ] New users automatically receive Free plan and 50-page quota.
- [ ] Users can view subscription status and quota usage in profile.
- [ ] Users can click the upgrade button to be redirected to the Creem Checkout page and complete payment with PayPal or credit card.
- [ ] Creem Webhooks (checkout.completed / subscription.* events) pass signature verification and correctly update subscription status and quota.
- [ ] Users can cancel or change subscriptions via the Creem Customer Portal; expired subscriptions automatically downgrade to Free.
- [ ] Upload is blocked with upgrade prompt when quota insufficient.
- [ ] Quota correctly deducted after task completion.
- [ ] Tasks exceeding retention period are automatically cleaned up.

### 12.2 Core Features

- [ ] Browser can upload scanned and text-based PDFs and successfully create tasks.
- [ ] Task list only displays current user's tasks with complete data isolation.
- [ ] Task list can display processing, completed, and failed statuses.
- [ ] Task list can display page analysis status and real-time current/total pages.
- [ ] Scanned PDFs can generate Markdown via RapidOCR; can use full-page rendering OCR fallback when no embedded images.
- [ ] Text-based PDFs can extract bookmarks and generate table of contents and chapter headings.
- [ ] Field tables, comparison tables, and response code tables in text-based PDFs can be converted to Markdown tables.
- [ ] Cross-row and cross-page table content maintains basic column relationships without extensive degradation to normal paragraphs.
- [ ] Code examples can be converted to Markdown code blocks with support for cross-page continuous content.
- [ ] Result page left sidebar displays Markdown heading table of contents, right side correctly renders body text.
- [ ] Browser preview correctly displays tables, code blocks, lists, headings, and paragraphs.
- [ ] Can copy full text and download `.md` file with complete content.
- [ ] Can delete tasks and clean up files (local or S3).

### 12.3 Deployment and Stability

- [ ] Service can start with one command via Docker Compose.
- [ ] Celery Workers can process multiple tasks in parallel.
- [ ] Single page OCR failure does not cause entire task failure or service crash.
- [ ] Creem Webhook correctly handles subscription events and rejects requests that fail signature verification.
- [ ] All authenticated endpoints correctly verify JWT Token.
