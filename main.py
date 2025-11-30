import os
import io
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import img2pdf
import PyPDF2
import fitz  # PyMuPDF
import aiofiles
from dotenv import load_dotenv

load_dotenv()

API_SECRET = os.getenv('API_SECRET')  # optional, set in your host env if you want auth

app = FastAPI(title='Digit5 Converter API')

# ensure outputs directory exists
OUTPUT_DIR = os.path.join(os.getcwd(), 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# serve generated files at /outputs/<filename>
app.mount('/outputs', StaticFiles(directory=OUTPUT_DIR), name='outputs')


def _check_secret(x_api_key: Optional[str]):
    if API_SECRET:
        if not x_api_key or x_api_key != API_SECRET:
            raise HTTPException(status_code=401, detail='Unauthorized')


def _unique_filename(prefix: str, ext: str):
    return f"{prefix}_{uuid.uuid4().hex}.{ext}"


@app.get('/health')
async def health():
    return {'ok': True, 'service': 'digit5-converter'}


@app.post('/convert/image-to-pdf')
async def image_to_pdf(request: Request, x_api_key: Optional[str] = Header(None), files: List[UploadFile] = File(...)):
    """Accepts one or more images (field name 'file') and returns a single PDF."""
    _check_secret(x_api_key)
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail='No files uploaded. Use field name "file".')

    images_bytes = []
    for f in files:
        content = await f.read()
        # verify it's an image
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception:
            raise HTTPException(status_code=400, detail=f'Uploaded file {f.filename} is not a valid image.')
        images_bytes.append(content)

    try:
        pdf_bytes = img2pdf.convert(images_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to convert image to pdf: {e}')

    out_name = _unique_filename('converted', 'pdf')
    out_path = os.path.join(OUTPUT_DIR, out_name)
    async with aiofiles.open(out_path, 'wb') as out_file:
        await out_file.write(pdf_bytes)

    # return a path under /outputs; your host base domain + /outputs/<file> will serve it
    return JSONResponse({'success': True, 'fileUrl': f"/outputs/{out_name}", 'filename': out_name})


@app.post('/convert/scan-to-pdf')
async def scan_to_pdf(request: Request, x_api_key: Optional[str] = Header(None), files: List[UploadFile] = File(...)):
    # alias of image_to_pdf (keeps same behavior)
    return await image_to_pdf(request, x_api_key, files)


@app.post('/convert/image-to-image')
async def image_to_image(request: Request, x_api_key: Optional[str] = Header(None),
                         file: UploadFile = File(...), target_format: str = Form(...)):
    """Convert single uploaded image to target format: png, webp, jpeg."""
    _check_secret(x_api_key)
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content)).convert('RGB')
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail='Invalid image file')

    target = target_format.lower().strip()
    if target not in ('png', 'jpeg', 'jpg', 'webp'):
        raise HTTPException(status_code=400, detail='Unsupported target_format. Use png, jpeg, webp')

    out_ext = 'jpg' if target in ('jpg', 'jpeg') else target
    out_name = _unique_filename('converted_img', out_ext)
    out_path = os.path.join(OUTPUT_DIR, out_name)

    img.save(out_path, format='JPEG' if out_ext == 'jpg' else out_ext.upper())
    return JSONResponse({'success': True, 'fileUrl': f"/outputs/{out_name}", 'filename': out_name})


@app.post('/convert/image-compress')
async def image_compress(request: Request, x_api_key: Optional[str] = Header(None),
                         file: UploadFile = File(...), quality: int = Form(75)):
    """Compress image (saves as JPG). quality 5-95"""
    _check_secret(x_api_key)
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content)).convert('RGB')
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail='Invalid image file')

    if quality < 5 or quality > 95:
        raise HTTPException(status_code=400, detail='Quality must be between 5 and 95')

    out_name = _unique_filename('compressed', 'jpg')
    out_path = os.path.join(OUTPUT_DIR, out_name)
    img.save(out_path, format='JPEG', quality=quality, optimize=True)
    return JSONResponse({'success': True, 'fileUrl': f"/outputs/{out_name}", 'filename': out_name, 'quality': quality})


@app.post('/convert/pdf-to-images')
async def pdf_to_images(request: Request, x_api_key: Optional[str] = Header(None), file: UploadFile = File(...)):
    """Return PNG images for each PDF page (list of file URLs)."""
    _check_secret(x_api_key)
    content = await file.read()
    try:
        doc = fitz.open(stream=content, filetype='pdf')
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid PDF file')

    out_files = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_bytes = pix.tobytes('png')
        out_name = _unique_filename(f'page_{i+1}', 'png')
        out_path = os.path.join(OUTPUT_DIR, out_name)
        async with aiofiles.open(out_path, 'wb') as f:
            await f.write(img_bytes)
        out_files.append(f"/outputs/{out_name}")

    return JSONResponse({'success': True, 'files': out_files, 'pageCount': len(out_files)})


@app.post('/convert/pdf-merge')
async def pdf_merge(request: Request, x_api_key: Optional[str] = Header(None), files: List[UploadFile] = File(...)):
    _check_secret(x_api_key)
    if not files or len(files) < 2:
        raise HTTPException(status_code=400, detail='Upload two or more PDF files to merge')

    merger = PyPDF2.PdfMerger()
    try:
        for f in files:
            content = await f.read()
            reader_stream = io.BytesIO(content)
            merger.append(reader_stream)
        out_name = _unique_filename('merged', 'pdf')
        out_path = os.path.join(OUTPUT_DIR, out_name)
        with open(out_path, 'wb') as out_f:
            merger.write(out_f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Merge error: {e}')
    finally:
        merger.close()

    return JSONResponse({'success': True, 'fileUrl': f"/outputs/{out_name}", 'filename': out_name})


@app.post('/convert/pdf-split')
async def pdf_split(request: Request, x_api_key: Optional[str] = Header(None), file: UploadFile = File(...)):
    _check_secret(x_api_key)
    content = await file.read()
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(content))
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid PDF file')

    out_files = []
    for i, page in enumerate(reader.pages):
        writer = PyPDF2.PdfWriter()
        writer.add_page(page)
        out_name = _unique_filename(f'split_{i+1}', 'pdf')
        out_path = os.path.join(OUTPUT_DIR, out_name)
        with open(out_path, 'wb') as out_f:
            writer.write(out_f)
        out_files.append(f"/outputs/{out_name}")

    return JSONResponse({'success': True, 'files': out_files, 'pageCount': len(out_files)})


@app.post('/cleanup')
async def cleanup(request: Request, x_api_key: Optional[str] = Header(None)):
    """Manually delete files in outputs/ (protect in production)."""
    _check_secret(x_api_key)
    removed = 0
    for fn in os.listdir(OUTPUT_DIR):
        try:
            os.remove(os.path.join(OUTPUT_DIR, fn))
            removed += 1
        except Exception:
            pass
    return JSONResponse({'success': True, 'removed': removed})
