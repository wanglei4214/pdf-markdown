# -*- coding: utf-8 -*-
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv('DB_PATH', Path(__file__).with_name('tasks.db')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect():
    """新建连接并开启 WAL 与忙等待，降低多线程并发读写时的锁冲突。"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def init_db():
    conn = _connect()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_page INTEGER DEFAULT 0,
            total_pages INTEGER DEFAULT 0,
            output_path TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def _now():
    return datetime.now().isoformat()


def create_task(original_name: str, file_size: int) -> dict:
    task_id = str(uuid.uuid4())
    now = _now()
    task = {
        'id': task_id,
        'filename': '',
        'original_name': original_name,
        'file_size': file_size,
        'status': 'pending',
        'current_page': 0,
        'total_pages': 0,
        'output_path': None,
        'error_message': None,
        'created_at': now,
        'updated_at': now,
    }
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO tasks (id, filename, original_name, file_size, status,
                           current_page, total_pages, output_path, error_message,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task['id'], task['filename'], task['original_name'], task['file_size'],
        task['status'], task['current_page'], task['total_pages'],
        task['output_path'], task['error_message'],
        task['created_at'], task['updated_at']
    ))
    conn.commit()
    conn.close()
    return task


def get_task(task_id: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tasks(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_task_status(task_id: str, **kwargs):
    allowed = {'status', 'current_page', 'total_pages', 'output_path', 'error_message', 'filename', 'file_size'}
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
