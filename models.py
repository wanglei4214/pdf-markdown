# -*- coding: utf-8 -*-
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv('DB_PATH', Path(__file__).with_name('tasks.db')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 每月页数配额：免费用户 200 页/月，Pro（已付费）用户 2000 页/月；按自然月重置
FREE_MONTHLY_PAGES = 200
PRO_MONTHLY_PAGES = 2000


def _current_month() -> str:
    return datetime.now().strftime('%Y-%m')


def _connect():
    """新建连接并开启 WAL 与忙等待，降低多线程并发读写时的锁冲突。"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def init_db():
    conn = _connect()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            google_sub TEXT NOT NULL UNIQUE,
            email TEXT,
            name TEXT,
            picture TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    # 兼容旧库：users 表已存在时补齐配额列
    existing_user_cols = {row[1] for row in conn.execute('PRAGMA table_info(users)')}
    if 'used_pages' not in existing_user_cols:
        conn.execute('ALTER TABLE users ADD COLUMN used_pages INTEGER NOT NULL DEFAULT 0')
    if 'quota_month' not in existing_user_cols:
        conn.execute('ALTER TABLE users ADD COLUMN quota_month TEXT')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_page INTEGER DEFAULT 0,
            total_pages INTEGER DEFAULT 0,
            reserved_pages INTEGER DEFAULT 0,
            output_path TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    # 兼容旧库：tasks 表已存在时补齐 user_id 列（历史任务 user_id 为空，登录用户不可见）
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(tasks)')}
    if 'user_id' not in existing_cols:
        conn.execute('ALTER TABLE tasks ADD COLUMN user_id TEXT')
    if 'reserved_pages' not in existing_cols:
        conn.execute('ALTER TABLE tasks ADD COLUMN reserved_pages INTEGER DEFAULT 0')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, created_at)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            request_id TEXT UNIQUE,
            creem_order_id TEXT,
            product_id TEXT,
            product_name TEXT,
            amount INTEGER,
            currency TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            checkout_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at)')
    conn.commit()
    conn.close()


def upsert_user(google_sub: str, email: str | None, name: str | None, picture: str | None) -> dict:
    """根据 Google 账号唯一标识 upsert 用户，返回用户记录。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE google_sub = ?', (google_sub,)).fetchone()
    now = _now()
    if row:
        conn.execute(
            'UPDATE users SET email = ?, name = ?, picture = ? WHERE id = ?',
            (email, name, picture, row['id'])
        )
        user = dict(row)
        user.update({'email': email, 'name': name, 'picture': picture})
    else:
        user = {
            'id': str(uuid.uuid4()),
            'google_sub': google_sub,
            'email': email,
            'name': name,
            'picture': picture,
            'created_at': now,
        }
        conn.execute('''
            INSERT INTO users (id, google_sub, email, name, picture, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user['id'], user['google_sub'], user['email'], user['name'],
              user['picture'], user['created_at']))
    conn.commit()
    conn.close()
    return user


def get_user(user_id: str) -> dict | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- 订单（Creem 支付） ----------

def create_order(user_id: str, request_id: str, product_id: str,
                 checkout_url: str) -> dict:
    order_id = str(uuid.uuid4())
    now = _now()
    order = {
        'id': order_id,
        'user_id': user_id,
        'request_id': request_id,
        'creem_order_id': None,
        'product_id': product_id,
        'product_name': None,
        'amount': None,
        'currency': None,
        'status': 'pending',
        'checkout_url': checkout_url,
        'created_at': now,
        'updated_at': now,
    }
    conn = _connect()
    conn.execute('''
        INSERT INTO orders (id, user_id, request_id, creem_order_id, product_id,
                            product_name, amount, currency, status, checkout_url,
                            created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', tuple(order[k] for k in (
        'id', 'user_id', 'request_id', 'creem_order_id', 'product_id',
        'product_name', 'amount', 'currency', 'status', 'checkout_url',
        'created_at', 'updated_at')))
    conn.commit()
    conn.close()
    return order


def get_user_by_customer(customer_id: str) -> dict | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE customer_id = ?', (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_order_by_request(request_id: str) -> dict | None:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM orders WHERE request_id = ?', (request_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_orders(user_id: str, limit: int = 50) -> list[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_order_paid(request_id: str, creem_order_id: str, product_name: str | None,
                    amount: int | None, currency: str | None) -> None:
    conn = _connect()
    conn.execute('''
        UPDATE orders
        SET status = 'paid', creem_order_id = ?, product_name = ?, amount = ?,
            currency = ?, updated_at = ?
        WHERE request_id = ?
    ''', (creem_order_id, product_name, amount, currency, _now(), request_id))
    conn.commit()
    conn.close()


def mark_orders_revoked(creem_order_id: str | None = None) -> None:
    """退款 / 订阅失效时，把对应已支付订单标记为失效。"""
    conn = _connect()
    if creem_order_id:
        conn.execute(
            "UPDATE orders SET status = 'revoked', updated_at = ? WHERE creem_order_id = ?",
            (_now(), creem_order_id))
    conn.commit()
    conn.close()


def revoke_order_by_request(request_id: str) -> None:
    """按幂等键撤销订单（订阅取消/退款事件回传 request_id 时使用）。"""
    conn = _connect()
    conn.execute(
        "UPDATE orders SET status = 'revoked', updated_at = ? WHERE request_id = ?",
        (_now(), request_id))
    conn.commit()
    conn.close()


def revoke_all_orders(user_id: str) -> None:
    """撤销用户全部已支付订单（兜底降级：事件只带 metadata.user_id 时使用）。"""
    conn = _connect()
    conn.execute(
        "UPDATE orders SET status = 'revoked', updated_at = ? WHERE user_id = ? AND status = 'paid'",
        (_now(), user_id))
    conn.commit()
    conn.close()


def _has_paid_order_conn(conn, user_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM orders WHERE user_id = ? AND status = 'paid' LIMIT 1",
        (user_id,)
    ).fetchone() is not None


def has_paid_order(user_id: str) -> bool:
    conn = _connect()
    result = _has_paid_order_conn(conn, user_id)
    conn.close()
    return result


# ---------- 页数配额 ----------

def quota_status(user_id: str) -> dict:
    """返回当前配额状态；跨月时自动清零并记录新月份。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT used_pages, quota_month FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    month = _current_month()
    if not row:
        conn.close()
        return {'plan': 'free', 'used_pages': 0, 'limit': FREE_MONTHLY_PAGES, 'month': month}
    used = row['used_pages'] if row['quota_month'] == month else 0
    if row['quota_month'] != month:
        conn.execute(
            'UPDATE users SET used_pages = 0, quota_month = ? WHERE id = ?', (month, user_id)
        )
        conn.commit()
    pro = _has_paid_order_conn(conn, user_id)
    conn.close()
    return {
        'plan': 'pro' if pro else 'free',
        'used_pages': used,
        'limit': PRO_MONTHLY_PAGES if pro else FREE_MONTHLY_PAGES,
        'month': month,
    }


def try_consume_pages(user_id: str, n: int) -> dict | None:
    """上传时预扣页数；剩余配额不足返回 None。跨月时先清零再扣。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT used_pages, quota_month FROM users WHERE id = ?', (user_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    month = _current_month()
    used = row['used_pages'] if row['quota_month'] == month else 0
    pro = _has_paid_order_conn(conn, user_id)
    limit = PRO_MONTHLY_PAGES if pro else FREE_MONTHLY_PAGES
    if used + n > limit:
        conn.close()
        return None
    conn.execute(
        'UPDATE users SET used_pages = ?, quota_month = ? WHERE id = ?', (used + n, month, user_id)
    )
    conn.commit()
    conn.close()
    return {'plan': 'pro' if pro else 'free', 'used_pages': used + n,
            'limit': limit, 'month': month}


def refund_pages(user_id: str, n: int) -> None:
    """任务失败时返还预扣页数。仅当仍处同一计费月时返还，避免冲减新周期配额。"""
    conn = _connect()
    current_month = _current_month()
    cursor = conn.execute(
        'UPDATE users SET used_pages = MAX(0, used_pages - ?) '
        'WHERE id = ? AND quota_month = ?',
        (n, user_id, current_month))
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    import logging
    logger = logging.getLogger(__name__)
    if affected_rows > 0:
        logger.info(f'已为用户 {user_id} 返还 {n} 页配额（当前月份：{current_month}）')
    else:
        logger.warning(f'用户 {user_id} 配额返还失败：可能不在同一计费月或用户不存在（尝试返还 {n} 页，当前月份：{current_month}）')


def _now():
    return datetime.now().isoformat()


def create_task(user_id: str, original_name: str, file_size: int, reserved_pages: int = 0) -> dict:
    task_id = str(uuid.uuid4())
    now = _now()
    task = {
        'id': task_id,
        'user_id': user_id,
        'filename': '',
        'original_name': original_name,
        'file_size': file_size,
        'status': 'pending',
        'current_page': 0,
        'total_pages': 0,
        'reserved_pages': reserved_pages,
        'output_path': None,
        'error_message': None,
        'created_at': now,
        'updated_at': now,
    }
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO tasks (id, user_id, filename, original_name, file_size, status,
                           current_page, total_pages, reserved_pages, output_path, error_message,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task['id'], task['user_id'], task['filename'], task['original_name'],
        task['file_size'], task['status'], task['current_page'], task['total_pages'],
        task['reserved_pages'], task['output_path'], task['error_message'],
        task['created_at'], task['updated_at']
    ))
    conn.commit()
    conn.close()
    return task


def get_task(task_id: str, user_id: str | None = None) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if user_id is None:
        row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    else:
        row = conn.execute(
            'SELECT * FROM tasks WHERE id = ? AND user_id = ?', (task_id, user_id)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tasks(user_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if user_id is None:
        rows = conn.execute(
            'SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (limit, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (user_id, limit, offset)
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_tasks_by_status(status: str) -> int:
    """统计指定状态的任务数量。"""
    conn = _connect()
    count = conn.execute('SELECT COUNT(*) FROM tasks WHERE status = ?', (status,)).fetchone()[0]
    conn.close()
    return count


def update_task_status(task_id: str, **kwargs):
    allowed = {'status', 'current_page', 'total_pages', 'reserved_pages', 'output_path', 'error_message', 'filename', 'file_size'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields['updated_at'] = _now()
    sets = ', '.join(f'{k} = ?' for k in fields)
    values = list(fields.values()) + [task_id]
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f'UPDATE tasks SET {sets} WHERE id = ?', values)
    conn.commit()
    conn.close()


def delete_task(task_id: str):
    conn = _connect()
    conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()


def mark_status_where(old_status: str, new_status: str, error_message: str | None = None):
    conn = _connect()
    now = _now()
    if error_message is not None:
        conn.execute(
            'UPDATE tasks SET status = ?, error_message = ?, updated_at = ? WHERE status = ?',
            (new_status, error_message, now, old_status)
        )
    else:
        conn.execute(
            'UPDATE tasks SET status = ?, updated_at = ? WHERE status = ?',
            (new_status, now, old_status)
        )
    conn.commit()
    conn.close()
