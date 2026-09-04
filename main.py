# -*- coding: utf-8 -*-
import os
import shutil
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import models
from ocr_engine import OcrEngine

app = FastAPI(title='PDF to Markdown')

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv('DATA_DIR', BASE_DIR))
UPLOAD_DIR = DATA_DIR / 'uploads'
OUTPUT_DIR = DATA_DIR / 'outputs'
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

models.init_db()

# 服务重启后，之前处于 processing 的任务实际上已停止，标记为失败并提示用户重试
models.mark_status_where(
    old_status='processing',
    new_status='failed',
    error_message='服务已重启，任务处理被中断。请点击“继续处理”重新启动。',
)

# 全局引擎，启动时初始化
_ocr_engine: OcrEngine | None = None
_engine_lock = threading.Lock()
_active_threads: dict[str, threading.Thread] = {}


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


@app.post('/api/tasks/upload')
async def upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='仅支持 PDF 文件')

    task = models.create_task(file.filename, 0)
    upload_path = UPLOAD_DIR / f"{task['id']}.pdf"

    try:
        with open(upload_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        models.delete_task(task['id'])
        raise HTTPException(status_code=500, detail=f'保存文件失败: {e}')
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
        models.update_task_status(task_id, status='processing', current_page=0, total_pages=0)
        engine = get_engine()
        engine.process_pdf(pdf_path, str(output_path), progress_callback=on_progress)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError('未能从 PDF 中识别到有效内容。可能的原因：\n'
                               '1. 该 PDF 是文字版而非扫描版，但文字层提取失败；\n'
                               '2. 页面为纯图片/图表，未包含可识别文本；\n'
                               '3. OCR 置信度阈值过滤掉了全部结果。')

        models.update_task_status(
            task_id,
            status='success',
            current_page=models.get_task(task_id)['total_pages'],
            output_path=str(output_path),
            error_message=None,
        )
    except Exception:
        models.update_task_status(
            task_id,
            status='failed',
            error_message=traceback.format_exc(),
        )
    finally:
        _active_threads.pop(task_id, None)


@app.get('/api/tasks')
async def list_tasks(limit: int = 100, offset: int = 0):
    return models.list_tasks(limit=limit, offset=offset)


@app.get('/api/tasks/{task_id}')
async def get_task(task_id: str):
    task = models.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')
    return task


@app.get('/api/tasks/{task_id}/markdown')
async def get_markdown(task_id: str):
    task = models.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')
    if not task['output_path'] or not Path(task['output_path']).exists():
        raise HTTPException(status_code=404, detail='Markdown 尚未生成')
    content = Path(task['output_path']).read_text(encoding='utf-8')
    return PlainTextResponse(content)


@app.get('/api/tasks/{task_id}/download')
async def download_markdown(task_id: str):
    task = models.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')
    if not task['output_path'] or not Path(task['output_path']).exists():
        raise HTTPException(status_code=404, detail='Markdown 尚未生成')
    original_name = Path(task['original_name']).stem + '.md'
    return FileResponse(task['output_path'], filename=original_name, media_type='text/markdown')


@app.delete('/api/tasks/{task_id}')
async def delete_task(task_id: str):
    task = models.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')

    upload_path = UPLOAD_DIR / f'{task_id}.pdf'
    output_path = OUTPUT_DIR / f'{task_id}.md'
    if upload_path.exists():
        upload_path.unlink()
    if output_path.exists():
        output_path.unlink()

    models.delete_task(task_id)
    return {'ok': True}


@app.post('/api/tasks/{task_id}/retry')
async def retry_task(task_id: str):
    task = models.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail='任务不存在')

    if task['status'] == 'success':
        raise HTTPException(status_code=400, detail='已成功的任务无需继续处理')

    if task_id in _active_threads and _active_threads[task_id].is_alive():
        raise HTTPException(status_code=400, detail='该任务正在处理中，请稍后再试')

    upload_path = UPLOAD_DIR / f'{task_id}.pdf'
    if not upload_path.exists():
        raise HTTPException(status_code=404, detail='上传的 PDF 文件已丢失，无法继续处理')

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


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
