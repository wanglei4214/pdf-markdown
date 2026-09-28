import os

import psutil

_proc = psutil.Process(os.getpid())


def mem():
    mi = _proc.memory_info()
    return mi.rss / 1024 / 1024, mi.peak_wset / 1024 / 1024 if hasattr(mi, "peak_wset") else mi.rss / 1024 / 1024


print("baseline RSS/peak      = %6.0f / %6.0f MB" % mem())
from rapidocr_onnxruntime import RapidOCR
print("after import rapidocr  = %6.0f / %6.0f MB" % mem())
eng = RapidOCR()
print("after RapidOCR() init  = %6.0f / %6.0f MB" % mem())
import fitz
import pypdf
import numpy
from PIL import Image
print("after fitz/pypdf/PIL   = %6.0f / %6.0f MB" % mem())

doc = fitz.open()
page = doc.new_page(width=595, height=842)
pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
img = numpy.frombuffer(pix.samples, dtype=numpy.uint8).reshape(pix.height, pix.width, pix.n)
print("after render 1 page    = %6.0f / %6.0f MB" % mem())
res, _ = eng(img)
print("after OCR 1 page       = %6.0f / %6.0f MB" % mem())
