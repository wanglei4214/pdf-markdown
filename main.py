# -*- coding: utf-8 -*-
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import threading
import traceback
import uuid
from pathlib import Path

import requests

logger = logging.getLogger('pdf2md')

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

import models
from ocr_engine import OcrEngine

# Google 登录配置：在 Google Cloud Console 创建 OAuth 2.0 Client ID（Web application），
# 并把访问来源（如 http://localhost:8000）加入 Authorized JavaScript origins。
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
SESSION_SECRET_KEY = os.getenv('SESSION_SECRET_KEY', 'dev-secret-change-me')

# Creem 支付配置：Dashboard → Developers 获取 API Key 与 Webhook Secret（whsec_ 开头）
CREEM_API_KEY = os.getenv('CREEM_API_KEY', '')
CREEM_WEBHOOK_SECRET = os.getenv('CREEM_WEBHOOK_SECRET', '')
# 在售商品 ID（Creem Dashboard → Products → Copy ID，prod_ 开头）
CREEM_PRODUCT_ID = os.getenv('CREEM_PRODUCT_ID', '')
# true 使用测试环境（test-api.creem.io），生产设为 false
CREEM_TEST_MODE = os.getenv('CREEM_TEST_MODE', 'true').lower() == 'true'
CREEM_API_BASE = 'https://test-api.creem.io/v1' if CREEM_TEST_MODE else 'https://api.creem.io/v1'

app = FastAPI(title='PDF to Markdown')

# 基于签名 cookie 的会话；生产环境务必通过环境变量设置 SESSION_SECRET_KEY
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    https_only=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
    same_site='lax',
)

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv('DATA_DIR', BASE_DIR))
UPLOAD_DIR = DATA_DIR / 'uploads'
OUTPUT_DIR = DATA_DIR / 'outputs'
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

LOG_DIR = DATA_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging():
    """配置日志：同时输出到控制台和文件（按大小轮转），文件位于数据目录 logs/。"""
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_DIR / 'app.log',
        maxBytes=10 * 1024 * 1024,  # 单文件 10 MB
        backupCount=5,              # 保留 5 个历史文件
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # 让 uvicorn 的访问/错误日志也写入同一文件
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.addHandler(console)
        lg.addHandler(file_handler)
        lg.propagate = False


setup_logging()
logger = logging.getLogger('pdf2md')

models.init_db()

# 服务重启后，之前处于 processing 的任务实际上已停止，标记为失败并提示用户重试
models.mark_status_where(
    old_status='processing',
    new_status='failed',
    error_message='The service was restarted and processing was interrupted. Please click "Resume" to restart.',
)

# 全局引擎，启动时初始化
_ocr_engine: OcrEngine | None = None
_engine_lock = threading.Lock()
_active_threads: dict[str, threading.Thread] = {}

# OCR 并发槽：RapidOCR/onnxruntime 实例非线程安全，且 OCR 内存占用高，
# 多个任务同时推理会导致 native 崩溃 / OOM。用信号量串行化，多余任务排队等待。
# 如需更高并发，应改用独立 worker 进程（Celery 等），而不是调大此值。
_max_concurrency = max(1, int(os.getenv('OCR_MAX_CONCURRENCY', '1')))
_ocr_slots = threading.BoundedSemaphore(_max_concurrency)


def get_engine() -> OcrEngine:
    global _ocr_engine
    if _ocr_engine is None:
        with _engine_lock:
            if _ocr_engine is None:
                _ocr_engine = OcrEngine()
    return _ocr_engine


app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')


@app.get('/')
async def root():
    return RedirectResponse(url='/static/index.html')


# ---------- 登录鉴权 ----------

def _public_user(user: dict) -> dict:
    return {k: user.get(k) for k in ('id', 'email', 'name', 'picture')}


def get_current_user(request: Request) -> dict:
    """从 session 中解析当前登录用户，未登录返回 401。"""
    user_id = request.session.get('user_id')
    user = models.get_user(user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail='Not logged in')
    return user


@app.get('/api/auth/config')
async def auth_config():
    """前端据此初始化 Google 登录按钮；未配置时隐藏登录入口。"""
    return {'google_client_id': GOOGLE_CLIENT_ID}


@app.post('/api/auth/google')
async def auth_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail='Google login is not configured on the server')
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid request body')
    credential = body.get('credential') if isinstance(body, dict) else None
    if not credential:
        raise HTTPException(status_code=400, detail='Missing credential')
    try:
        # 验证 JWT 签名、过期时间与受众（aud 必须是本应用的 Client ID）
        info = id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        if info.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
            raise ValueError('Invalid token issuer')
        if not info.get('email_verified'):
            raise ValueError('Google account email is not verified')
    except ValueError:
        logger.warning('Google ID Token 校验失败', exc_info=True)
        raise HTTPException(status_code=401, detail='Google login verification failed')
    user = models.upsert_user(
        google_sub=info['sub'],
        email=info.get('email'),
        name=info.get('name'),
        picture=info.get('picture'),
    )
    request.session['user_id'] = user['id']
    logger.info('用户登录：%s (%s)', user.get('email'), user['id'])
    return {'user': _public_user(user)}


@app.get('/api/auth/me')
async def auth_me(request: Request):
    user_id = request.session.get('user_id')
    user = models.get_user(user_id) if user_id else None
    if not user:
        return {'user': None}
    return {'user': _public_user(user)}


@app.post('/api/auth/logout')
async def auth_logout(request: Request):
    request.session.clear()
    return {'ok': True}


# ---------- Creem 支付 ----------

@app.get('/api/payments/config')
async def payments_config(user: dict = Depends(get_current_user)):
    """前端据此决定是否显示付费按钮。"""
    return {
        'enabled': bool(CREEM_API_KEY and CREEM_PRODUCT_ID),
        'test_mode': CREEM_TEST_MODE,
        'has_paid': models.has_paid_order(user['id']),
    }


@app.post('/api/payments/checkout')
async def create_checkout(request: Request, user: dict = Depends(get_current_user)):
    """创建 Creem 结账会话，返回结账页 URL，前端跳转过去完成付款。"""
    if not CREEM_API_KEY or not CREEM_PRODUCT_ID:
        raise HTTPException(status_code=503, detail='Payment is not configured on the server')

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    product_id = (body or {}).get('product_id') or CREEM_PRODUCT_ID

    # request_id 是我们本地订单的幂等键，会原样出现在 webhook 的 object.request_id 中
    request_id = f'order_{user["id"]}_{uuid.uuid4().hex[:16]}'
    base_url = str(request.base_url).rstrip('/')

    try:
        resp = requests.post(
            f'{CREEM_API_BASE}/checkouts',
            headers={'x-api-key': CREEM_API_KEY, 'Content-Type': 'application/json'},
            json={
                'product_id': product_id,
                'request_id': request_id,
                'success_url': f'{base_url}/static/index.html?checkout=success',
                # metadata 会随 webhook 事件一起回来，用于定位本地用户
                'metadata': {'user_id': user['id'], 'user_email': user.get('email')},
                'customer': {'email': user.get('email') or ''},
            },
            timeout=15,
        )
    except requests.RequestException:
        logger.exception('调用 Creem 创建结账会话失败')
        raise HTTPException(status_code=502, detail='Payment service is unavailable')

    if resp.status_code != 200:
        logger.error('Creem 创建结账失败：%s %s', resp.status_code, resp.text[:500])
        raise HTTPException(status_code=502, detail='Failed to create checkout session')

    data = resp.json()
    checkout_url = data.get('checkout_url')
    if not checkout_url:
        raise HTTPException(status_code=502, detail='No checkout URL returned by payment service')

    models.create_order(user['id'], request_id, product_id, checkout_url)
    return {'checkout_url': checkout_url, 'request_id': request_id}


@app.get('/api/payments/orders')
async def list_payment_orders(user: dict = Depends(get_current_user)):
    return models.list_orders(user['id'])


@app.post('/api/webhooks/creem')
async def creem_webhook(request: Request):
    """Creem 事件回调：必须用原始请求体验签，不能先解析 JSON。"""
    raw_body = await request.body()
    signature = request.headers.get('creem-signature', '')

    if not CREEM_WEBHOOK_SECRET:
        logger.error('收到 Creem webhook 但服务器未配置 CREEM_WEBHOOK_SECRET')
        raise HTTPException(status_code=503, detail='Webhook is not configured')

    # HMAC-SHA256(webhook_secret, 原始 body) 的 hex，与 creem-signature 头恒定时间比较
    expected = hmac.new(
        CREEM_WEBHOOK_SECRET.encode('utf-8'), raw_body, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        logger.warning('Creem webhook 签名校验失败')
        raise HTTPException(status_code=400, detail='Invalid signature')

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid payload')

    event_type = event.get('eventType', '')
    obj = event.get('object') or {}
    logger.info('收到 Creem 事件：%s (%s)', event_type, event.get('id'))

    try:
        if event_type == 'checkout.completed':
            order = obj.get('order') or {}
            if order.get('status') != 'paid':
                logger.info('结账订单状态为 %s，暂不开通权益', order.get('status'))
                return {'ok': True}

            request_id = obj.get('request_id') or ''
            local_order = models.get_order_by_request(request_id)
            if not local_order:
                logger.error('Webhook 找不到本地订单：request_id=%s', request_id)
                return {'ok': True}  # 返回 200 避免 Creem 无谓重试
            # 幂等：已支付的重复事件直接忽略
            if local_order['status'] != 'paid':
                product = obj.get('product') or {}
                models.mark_order_paid(
                    request_id=request_id,
                    creem_order_id=order.get('id'),
                    product_name=product.get('name'),
                    amount=order.get('amount'),
                    currency=order.get('currency'),
                )
                logger.info('支付成功，已开通权益：request_id=%s order=%s',
                            request_id, order.get('id'))

        elif event_type in ('subscription.canceled', 'subscription.expired',
                            'subscription.unpaid', 'refund.created'):
            # 退款 / 订阅失效：撤销权益。退款事件里订单号在 object.order.id
            order_info = obj.get('order') or {}
            models.mark_orders_revoked(creem_order_id=order_info.get('id') or obj.get('id'))
            logger.info('已撤销权益：事件=%s', event_type)
        # 其余事件（subscription.paid / active / past_due 等）当前无需处理
    except Exception:
        logger.exception('处理 Creem webhook 失败')
        # 返回 500 让 Creem 按退避策略重试（最多 5 次）
        raise HTTPException(status_code=500, detail='Webhook handling failed')

    return {'ok': True}


# ---------- 任务接口（均需登录，且只能访问自己的任务） ----------

@app.post('/api/tasks/upload')
async def upload(file: UploadFile = File(...),
                 user: dict = Depends(get_current_user)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='Only PDF files are supported')

    task = models.create_task(user['id'], file.filename, 0)
    upload_path = UPLOAD_DIR / f"{task['id']}.pdf"

    try:
        with open(upload_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        models.delete_task(task['id'])
        raise HTTPException(status_code=500, detail=f'Failed to save file: {e}')
    finally:
        file.file.close()

    file_size = upload_path.stat().st_size
    models.update_task_status(task['id'], filename=str(upload_path), file_size=file_size)

    # 后台启动 OCR
    _start_ocr_thread(task['id'], str(upload_path))

    return models.get_task(task['id'])


def _start_ocr_thread(task_id: str, pdf_path: str):
    """启动一个后台 OCR 线程，并记录到活跃线程表。"""
    thread = threading.Thread(target=_run_ocr, args=(task_id, pdf_path), daemon=True)
    _active_threads[task_id] = thread
    thread.start()


def _run_ocr(task_id: str, pdf_path: str):
    output_path = OUTPUT_DIR / f'{task_id}.md'

    def on_progress(current: int, total: int):
        models.update_task_status(
            task_id,
            status='processing',
            current_page=current,
            total_pages=total,
        )

    try:
        # 排队等待 OCR 槽位；等待期间保持 pending，拿到槽位后才标记 processing。
        # 这样多个任务会串行执行，避免并发调用同一 OCR 实例导致 native 崩溃 / OOM。
        logger.info('任务 %s 等待 OCR 槽位（并发上限 %d）', task_id, _max_concurrency)
        _ocr_slots.acquire()
        try:
            logger.info('任务 %s 开始处理', task_id)
            models.update_task_status(task_id, status='processing', current_page=0, total_pages=0)
            engine = get_engine()
            engine.process_pdf(pdf_path, str(output_path), progress_callback=on_progress)
        finally:
            _ocr_slots.release()

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError('No valid content could be recognized from the PDF. Possible reasons:\n'
                               '1. The PDF is text-based rather than scanned, but text-layer extraction failed;\n'
                               '2. The pages contain only images/charts with no recognizable text;\n'
                               '3. The OCR confidence threshold filtered out all results.')

        models.update_task_status(
            task_id,
            status='success',
            current_page=models.get_task(task_id)['total_pages'],
            output_path=str(output_path),
            error_message=None,
        )
        logger.info('任务 %s 处理完成', task_id)
    except Exception:
        logger.exception('任务 %s 处理失败', task_id)
        models.update_task_status(
            task_id,
            status='failed',
            error_message=traceback.format_exc(),
        )
    finally:
        _active_threads.pop(task_id, None)


@app.get('/api/tasks')
async def list_tasks(limit: int = 100, offset: int = 0,
                     user: dict = Depends(get_current_user)):
    return models.list_tasks(user_id=user['id'], limit=limit, offset=offset)


def _get_owned_task(task_id: str, user: dict) -> dict:
    task = models.get_task(task_id, user_id=user['id'])
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')
    return task


@app.get('/api/tasks/{task_id}')
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    return _get_owned_task(task_id, user)


@app.get('/api/tasks/{task_id}/markdown')
async def get_markdown(task_id: str, user: dict = Depends(get_current_user)):
    task = _get_owned_task(task_id, user)
    if not task['output_path'] or not Path(task['output_path']).exists():
        raise HTTPException(status_code=404, detail='Markdown has not been generated yet')
    content = Path(task['output_path']).read_text(encoding='utf-8')
    return PlainTextResponse(content)


@app.get('/api/tasks/{task_id}/download')
async def download_markdown(task_id: str, user: dict = Depends(get_current_user)):
    task = _get_owned_task(task_id, user)
    if not task['output_path'] or not Path(task['output_path']).exists():
        raise HTTPException(status_code=404, detail='Markdown has not been generated yet')
    original_name = Path(task['original_name']).stem + '.md'
    return FileResponse(task['output_path'], filename=original_name, media_type='text/markdown')


@app.delete('/api/tasks/{task_id}')
async def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    task = _get_owned_task(task_id, user)

    upload_path = UPLOAD_DIR / f'{task_id}.pdf'
    output_path = OUTPUT_DIR / f'{task_id}.md'
    if upload_path.exists():
        upload_path.unlink()
    if output_path.exists():
        output_path.unlink()

    models.delete_task(task_id)
    return {'ok': True}


@app.post('/api/tasks/{task_id}/retry')
async def retry_task(task_id: str, user: dict = Depends(get_current_user)):
    task = _get_owned_task(task_id, user)

    if task['status'] == 'success':
        raise HTTPException(status_code=400, detail='This task has already completed successfully')

    if task_id in _active_threads and _active_threads[task_id].is_alive():
        raise HTTPException(status_code=400, detail='This task is currently being processed, please try again later')

    upload_path = UPLOAD_DIR / f'{task_id}.pdf'
    if not upload_path.exists():
        raise HTTPException(status_code=404, detail='The uploaded PDF file is missing and processing cannot continue')

    output_path = OUTPUT_DIR / f'{task_id}.md'
    if output_path.exists():
        output_path.unlink()

    models.update_task_status(
        task_id,
        status='pending',
        current_page=0,
        total_pages=0,
        output_path=None,
        error_message=None,
    )
    _start_ocr_thread(task_id, str(upload_path))
    return models.get_task(task_id)


def _resume_pending_tasks():
    """服务重启后，重新入队重启前处于排队（pending）且已上传文件的任务。"""
    for task in models.list_tasks(limit=500):
        if task['status'] != 'pending':
            continue
        pdf_path = task.get('filename') or ''
        if pdf_path and Path(pdf_path).exists():
            logger.info('重启恢复：任务 %s 重新入队', task['id'])
            _start_ocr_thread(task['id'], pdf_path)


_resume_pending_tasks()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
