# -*- coding: utf-8 -*-
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import shutil
import threading
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests
from pypdf import PdfReader

logger = logging.getLogger('pdf2md')

from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import models
from ocr_engine import OcrEngine

# Google 登录配置：在 Google Cloud Console 创建 OAuth 2.0 Client ID（Web application），
# 并把访问来源（如 http://localhost:8000）加入 Authorized JavaScript origins。
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = 'https://pdf.my99ai.com/api/auth/google/callback'
GOOGLE_OAUTH_STATE_COOKIE = 'google_oauth_state'
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
_cancelled_tasks: set[str] = set()  # 被取消的任务ID集合
_cancelled_lock = threading.Lock()

# OCR 并发槽：支持 2 个任务并行处理，更多任务排队等待。
# 使用时间片轮转调度，保证所有用户公平获得处理机会（防止单用户霸占队列）。
_max_concurrency = max(1, int(os.getenv('OCR_MAX_CONCURRENCY', '2')))
_ocr_slots = threading.BoundedSemaphore(_max_concurrency)

# 时间片轮转调度：任务队列按用户分组，轮流从每个用户的队列中取任务
import collections
_user_task_queues: dict[str, collections.deque] = {}  # user_id -> deque[(task_id, pdf_path, reserved_pages)]
_queue_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_running = False


def get_engine() -> OcrEngine:
    global _ocr_engine
    if _ocr_engine is None:
        with _engine_lock:
            if _ocr_engine is None:
                _ocr_engine = OcrEngine()
    return _ocr_engine


def _start_scheduler():
    """启动时间片轮转调度线程（全局单例）。"""
    global _scheduler_thread, _scheduler_running
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_round_robin_scheduler, daemon=True)
    _scheduler_thread.start()
    logger.info('时间片轮转调度器已启动（并发槽位=%d）', _max_concurrency)


def _round_robin_scheduler():
    """时间片轮转调度器：轮流从每个用户的任务队列中取任务执行。"""
    while _scheduler_running:
        with _queue_lock:
            # 获取所有有任务的用户列表
            active_users = [uid for uid, q in _user_task_queues.items() if q]
            if not active_users:
                # 无任务时短暂休眠
                pass
            else:
                # 轮转：从第一个用户队列取任务
                user_id = active_users[0]
                queue = _user_task_queues[user_id]
                task_id, pdf_path, reserved_pages = queue.popleft()
                
                # 如果该用户队列空了，删除
                if not queue:
                    del _user_task_queues[user_id]
                
                # 启动 OCR 线程处理该任务
                thread = threading.Thread(
                    target=_run_ocr,
                    args=(task_id, pdf_path, reserved_pages),
                    daemon=True
                )
                _active_threads[task_id] = thread
                thread.start()
                logger.info('调度器分配任务 %s 给用户 %s', task_id, user_id)
        
        # 短暂休眠避免 CPU 空转
        threading.Event().wait(0.5)


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


@app.get('/api/auth/google/login')
async def google_login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail='Google OAuth is not configured on the server')

    state = secrets.token_urlsafe(32)
    params = urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    })
    redirect = RedirectResponse(
        url=f'https://accounts.google.com/o/oauth2/v2/auth?{params}',
        status_code=302,
    )
    redirect.set_cookie(
        GOOGLE_OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
        samesite='lax',
    )
    return redirect


@app.get('/api/auth/google/callback')
async def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse('/?login=failed', status_code=302)
    expected_state = request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)
    if not code or not state or not expected_state or not hmac.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail='Invalid Google OAuth state')
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail='Google OAuth is not configured on the server')

    try:
        token_response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code': code,
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'redirect_uri': GOOGLE_REDIRECT_URI,
                'grant_type': 'authorization_code',
            },
            timeout=15,
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        info_response = requests.get(
            'https://openidconnect.googleapis.com/v1/userinfo',
            headers={'Authorization': f"Bearer {tokens['access_token']}"},
            timeout=15,
        )
        info_response.raise_for_status()
        info = info_response.json()
    except (requests.RequestException, KeyError, ValueError):
        logger.exception('Google OAuth 登录失败')
        return RedirectResponse('/?login=failed', status_code=302)

    if not info.get('sub') or not info.get('email_verified'):
        return RedirectResponse('/?login=failed', status_code=302)

    user = models.upsert_user(
        google_sub=info['sub'],
        email=info.get('email'),
        name=info.get('name'),
        picture=info.get('picture'),
    )
    request.session['user_id'] = user['id']
    logger.info('用户登录：%s (%s)', user.get('email'), user['id'])
    redirect = RedirectResponse('/?login=success', status_code=302)
    redirect.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE)
    return redirect


@app.get('/api/auth/config')
async def auth_config():
    return {'google_client_id': GOOGLE_CLIENT_ID}


@app.post('/api/auth/google')
async def auth_google(request: Request):
    raise HTTPException(status_code=410, detail='Google ID token login is no longer supported')


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
    """前端据此决定是否显示付费按钮，并展示页数配额。"""
    return {
        'enabled': bool(CREEM_API_KEY and CREEM_PRODUCT_ID),
        'test_mode': CREEM_TEST_MODE,
        'has_paid': models.has_paid_order(user['id']),
        'quota': models.quota_status(user['id']),
    }


@app.post('/api/billing/checkout')
async def create_checkout(request: Request, user: dict = Depends(get_current_user)):
    """创建 Creem 结账会话，返回结账页 URL，前端跳转过去完成付款。
    
    注意：原接口路径 /api/payments/checkout 已废弃，请统一使用 /api/billing/checkout。
    """
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
                'success_url': f'{base_url}/?checkout=success',
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


@app.post('/api/payments/checkout')
async def create_checkout_legacy(request: Request, user: dict = Depends(get_current_user)):
    """旧接口兼容：重定向到新接口"""
    return await create_checkout(request, user)


@app.get('/api/payments/orders')
async def list_payment_orders(user: dict = Depends(get_current_user)):
    return models.list_orders(user['id'])


@app.post('/api/billing/claim')
async def claim_checkout(request: Request, user: dict = Depends(get_current_user)):
    """匿名直付成功后的认领：校验 Creem checkout，补建本地订单并开通权益。"""
    if not CREEM_API_KEY:
        raise HTTPException(status_code=503, detail='Payment is not configured on the server')
    try:
        body = await request.json()
    except Exception:
        body = {}
    checkout_id = ((body or {}).get('checkout_id') or '').strip()
    if not checkout_id:
        raise HTTPException(status_code=400, detail='checkout_id required')
    try:
        resp = requests.get(f'{CREEM_API_BASE}/checkouts/{checkout_id}',
                            headers={'x-api-key': CREEM_API_KEY}, timeout=15)
    except requests.RequestException:
        logger.exception('查询 Creem checkout 失败')
        raise HTTPException(status_code=502, detail='Payment service is unavailable')
    if resp.status_code != 200:
        logger.error('查询 Creem checkout 失败：%s %s', resp.status_code, resp.text[:500])
        raise HTTPException(status_code=502, detail='Failed to verify checkout')
    data = resp.json()
    ck = data.get('checkout') if isinstance(data.get('checkout'), dict) else data
    if ck.get('status') != 'processed':
        return {'ok': False, 'error': f"checkout status is {ck.get('status')}"}
    product = ck.get('product') or {}
    if CREEM_PRODUCT_ID and product.get('id') and product['id'] != CREEM_PRODUCT_ID:
        return {'ok': False, 'error': 'product mismatch'}

    request_id = f'claim_{checkout_id}'
    if not models.get_order_by_request(request_id):
        models.create_order(user['id'], request_id, product.get('id') or CREEM_PRODUCT_ID, '')
    order_info = ck.get('order') or {}
    models.mark_order_paid(
        request_id=request_id,
        creem_order_id=order_info.get('id') or checkout_id,
        product_name=product.get('name'),
        amount=ck.get('amount') or order_info.get('amount'),
        currency=ck.get('currency') or order_info.get('currency'),
    )
    # 记录 Creem 客户号，便于续费/退款事件匹配到用户
    customer_id = ck.get('customer_id') or (ck.get('customer') or {}).get('id')
    if customer_id:
        conn = models._connect()
        conn.execute('UPDATE users SET customer_id = ? WHERE id = ?', (customer_id, user['id']))
        conn.commit()
        conn.close()
    logger.info('checkout 认领成功：%s → 用户 %s', checkout_id, user['id'])
    return {'ok': True}


@app.post('/api/creem/webhook')
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

        elif event_type in ('subscription.paid', 'subscription.active'):
            # 订阅续费 / 激活：本地有对应订单则标记为已支付，保证续期后权益不中断
            request_id = obj.get('request_id') or ''
            if request_id:
                local_order = models.get_order_by_request(request_id)
                if local_order and local_order['status'] != 'paid':
                    order = obj.get('order') or {}
                    models.mark_order_paid(
                        request_id=request_id,
                        creem_order_id=order.get('id') or obj.get('id'),
                        product_name=(obj.get('product') or {}).get('name'),
                        amount=order.get('amount'),
                        currency=order.get('currency'),
                    )
                    logger.info('订阅续期，权益已延续：request_id=%s', request_id)

        elif event_type in ('subscription.canceled', 'subscription.expired',
                            'subscription.unpaid', 'refund.created'):
            # 退款 / 订阅失效：撤销权益。依次尝试 request_id、订单号、metadata.user_id 兜底，
            # 避免续费单号与初次下单不一致导致漏降级
            request_id = obj.get('request_id') or ''
            if request_id:
                models.revoke_order_by_request(request_id)
            order_info = obj.get('order') or {}
            models.mark_orders_revoked(creem_order_id=order_info.get('id') or obj.get('id'))
            metadata = obj.get('metadata') or {}
            if metadata.get('user_id'):
                models.revoke_all_orders(metadata['user_id'])
            customer = obj.get('customer') or {}
            if customer.get('id'):
                u = models.get_user_by_customer(customer['id'])
                if u:
                    models.revoke_all_orders(u['id'])
            logger.info('已撤销权益：事件=%s', event_type)
        # 其余事件（如 subscription.past_due）当前无需处理
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

    # 【第一阶段】上传前粗略检查：用户至少有 1 页额度
    quota = models.quota_status(user['id'])
    remaining = quota['limit'] - quota['used_pages']
    if remaining <= 0:
        logger.info('用户 %s 配额已用尽，拒绝上传：%d/%d', user['id'], quota['used_pages'], quota['limit'])
        raise HTTPException(status_code=403, detail={
            'error': 'quota_exhausted',
            'message': 'Your quota has been exhausted. Please purchase a plan to continue.',
            **quota,
        })

    # 【第二阶段】上传后精确检查：读取实际页数并扣费
    # 先创建任务（reserved_pages=0），上传文件后再更新 reserved_pages
    task = models.create_task(user['id'], file.filename, 0, reserved_pages=0)
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

    # 【第二阶段】上传后精确检查：读取实际页数并扣费
    # 数页失败的文件按 1 页放行（损坏文件会在 OCR 阶段失败并返还配额）。
    try:
        page_count = len(PdfReader(str(upload_path)).pages)
    except Exception:
        page_count = 1
        logger.warning('无法读取 PDF 页数，按 1 页计：%s', upload_path)

    consume = models.try_consume_pages(user['id'], page_count)
    if consume is None:
        # 精确检查后发现配额不足，回滚文件和任务
        quota = models.quota_status(user['id'])
        remaining = max(0, quota['limit'] - quota['used_pages'])
        upload_path.unlink(missing_ok=True)
        models.delete_task(task['id'])
        logger.info('用户 %s 配额不足：需 %d 页，剩余 %d/%d', user['id'], page_count,
                    remaining, quota['limit'])
        raise HTTPException(status_code=403, detail={
            'error': 'quota_exceeded',
            'message': f'This PDF has {page_count} pages, but you only have {remaining} pages remaining. Please purchase a plan.',
            'pages_required': page_count,
            'pages_remaining': remaining,
            **quota,
        })

    # 扣费成功，更新任务的 reserved_pages 和文件信息
    models.update_task_status(task['id'], filename=str(upload_path), file_size=file_size, reserved_pages=page_count)
    
    # 后台启动 OCR
    _start_ocr_thread(task['id'], str(upload_path), reserved_pages=page_count)

    return models.get_task(task['id'])


def _start_ocr_thread(task_id: str, pdf_path: str, reserved_pages: int = 0):
    """将任务加入用户队列，由调度器统一分配（时间片轮转，保证公平性）。"""
    task = models.get_task(task_id)
    if not task or not task.get('user_id'):
        logger.error('任务 %s 无法找到或缺少 user_id', task_id)
        return
    
    user_id = task['user_id']
    with _queue_lock:
        if user_id not in _user_task_queues:
            _user_task_queues[user_id] = collections.deque()
        _user_task_queues[user_id].append((task_id, pdf_path, reserved_pages))
        queue_pos = sum(len(q) for q in _user_task_queues.values())
    
    logger.info('任务 %s 已加入用户 %s 的队列（全局队列位置: %d）', task_id, user_id, queue_pos)
    _start_scheduler()


def _run_ocr(task_id: str, pdf_path: str, reserved_pages: int = 0):
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
            # 【关键修复】获取槽位后检查任务是否已被取消
            with _cancelled_lock:
                if task_id in _cancelled_tasks:
                    logger.info('任务 %s 已被取消，跳过处理', task_id)
                    _cancelled_tasks.discard(task_id)
                    return
            
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
        if reserved_pages:
            task = models.get_task(task_id)
            if task and task.get('user_id'):
                models.refund_pages(task['user_id'], reserved_pages)
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


@app.get('/api/tasks/queue/status')
async def queue_status(user: dict = Depends(get_current_user)):
    """返回当前用户的排队状态：队列中的任务数、预计等待时间等。"""
    with _queue_lock:
        # 统计全局队列中的任务总数
        total_queued = sum(len(q) for q in _user_task_queues.values())
        # 统计当前用户队列中的任务数
        user_queued = len(_user_task_queues.get(user['id'], []))
    
    # 统计正在处理的任务数（查询数据库中 status='processing' 的任务）
    processing_count = models.count_tasks_by_status('processing')
    
    # 计算当前用户在全局队列中的位置（时间片轮转算法）
    # 按用户轮转，所以位置 = 当前用户前面有多少个用户有任务
    users_ahead = 0
    with _queue_lock:
        for uid in _user_task_queues.keys():
            if uid == user['id']:
                break
            users_ahead += 1
    
    # 预估等待时间：假设每个任务平均处理时间 30 秒
    # 时间片轮转下，用户获得处理机会的周期 = 用户数 * 平均处理时间 / 并发数
    estimated_wait_minutes = 0
    if user_queued > 0 and users_ahead > 0:
        estimated_wait_minutes = (users_ahead * 0.5) + (user_queued * 0.5)
    
    return {
        'user_queued': user_queued,
        'total_queued': total_queued,
        'processing_count': processing_count,
        'max_concurrency': _max_concurrency,
        'estimated_wait_minutes': round(estimated_wait_minutes, 1) if estimated_wait_minutes > 0 else 0,
    }


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
    task_status = task['status']
    total_pages = task.get('total_pages', 0) or 0

    # 【关键修复1】从队列中移除任务，避免调度器继续处理已删除的任务
    user_id = task['user_id']
    refund_pages_count = 0
    with _queue_lock:
        if user_id in _user_task_queues:
            # 从用户队列中移除该任务（按 task_id 匹配），同时获取预扣页数
            queue = _user_task_queues[user_id]
            new_queue = collections.deque()
            for item in queue:
                if item[0] == task_id:
                    refund_pages_count = item[2]  # reserved_pages
                else:
                    new_queue.append(item)
            _user_task_queues[user_id] = new_queue
            # 如果队列为空，删除该用户的队列记录
            if not _user_task_queues[user_id]:
                del _user_task_queues[user_id]
    
    # 【关键修复2】如果任务正在处理，标记为取消，线程会在获取槽位后检查并提前退出
    if task_id in _active_threads and _active_threads[task_id].is_alive():
        with _cancelled_lock:
            _cancelled_tasks.add(task_id)
        logger.info('任务 %s 正在处理，已标记为取消', task_id)
    
    # 清理活动线程记录
    _active_threads.pop(task_id, None)
    
    # 【配额返还逻辑】未完成的任务返还页数
    # - pending/processing: 优先使用数据库中的 reserved_pages，fallback 到队列中的 reserved_pages，最后才用 total_pages
    # - failed: 失败时已经返还过，不重复返还
    # - success: 已完成的任务不返还
    if task_status in ('pending', 'processing'):
        # 优先使用数据库中持久化的 reserved_pages
        reserved_pages = task.get('reserved_pages', 0) or 0
        pages_to_refund = reserved_pages if reserved_pages > 0 else (refund_pages_count if refund_pages_count > 0 else total_pages)
        if pages_to_refund > 0:
            models.refund_pages(user_id, pages_to_refund)
            logger.info('任务 %s 未完成，返还 %d 页配额（status=%s, reserved_pages=%d, refund_pages_count=%d, total_pages=%d）', 
                       task_id, pages_to_refund, task_status, reserved_pages, refund_pages_count, total_pages)
        else:
            logger.warning('任务 %s 未完成但无需返还：pages_to_refund=0（reserved_pages=%d, refund_pages_count=%d, total_pages=%d）',
                          task_id, reserved_pages, refund_pages_count, total_pages)
    
    # 删除文件
    upload_path = UPLOAD_DIR / f'{task_id}.pdf'
    output_path = OUTPUT_DIR / f'{task_id}.md'
    if upload_path.exists():
        upload_path.unlink()
    if output_path.exists():
        output_path.unlink()

    # 删除数据库记录
    models.delete_task(task_id)
    logger.info('任务 %s 已删除并从队列中移除', task_id)
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
