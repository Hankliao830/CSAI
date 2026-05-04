import os
import subprocess
import tempfile
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import pytesseract
from PIL import Image
import fitz  # PyMuPDF

app = Flask(__name__)
CORS(app)

def eps_to_image(eps_path):
    """Convert EPS to PNG using Ghostscript"""
    out_path = eps_path.replace('.eps', '.png')
    subprocess.run([
        'gs', '-dNOPAUSE', '-dBATCH', '-dSAFER',
        '-sDEVICE=png16m', '-r150',
        '-sOutputFile=' + out_path,
        eps_path
    ], check=True, capture_output=True)
    return out_path

def pdf_to_image(pdf_path):
    """Convert first page of PDF to PNG using PyMuPDF"""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(2, 2)  # 2x zoom = 144dpi
    pix = page.get_pixmap(matrix=mat)
    out_path = pdf_path.replace('.pdf', '.png')
    pix.save(out_path)
    doc.close()
    return out_path

def ocr_image(img_path):
    """OCR an image, return text"""
    img = Image.open(img_path)
    text = pytesseract.image_to_string(img, lang='chi_tra+eng')
    return text

def diff_texts(eps_text, pdf_text):
    """Compare two OCR texts and return structured diff"""
    def clean(t):
        return [l.strip() for l in t.split('\n') if len(l.strip()) > 2]

    eps_lines = clean(eps_text)
    pdf_lines = clean(pdf_text)

    differences = []
    warnings = []
    confirmed = []

    def similar(a, b):
        if a == b: return 1.0
        if a in b or b in a: return 0.9
        # Simple char overlap
        sa, sb = set(a), set(b)
        if not sa or not sb: return 0
        return len(sa & sb) / len(sa | sb)

    for line in pdf_lines:
        best = max(eps_lines, key=lambda el: similar(el, line), default=None)
        if best and similar(best, line) > 0.75:
            if best == line:
                confirmed.append({'field': '文字行', 'value': line[:80]})
            else:
                differences.append({
                    'field': '文字差異',
                    'pdf_value': line[:80],
                    'eps_value': best[:80],
                    'severity': 'medium'
                })
        else:
            warnings.append({
                'field': 'PDF 有，EPS 未確認',
                'reason': line[:80],
                'suggestion': '請人工對照 EPS 確認'
            })

    for line in eps_lines:
        best = max(pdf_lines, key=lambda pl: similar(pl, line), default=None)
        if not best or similar(best, line) <= 0.75:
            differences.append({
                'field': 'EPS 獨有文字',
                'eps_value': line[:80],
                'pdf_value': '（PDF 中未找到）',
                'severity': 'high'
            })

    high = len([d for d in differences if d['severity'] == 'high'])
    verdict = 'fail' if high > 0 else 'warn' if differences or warnings else 'pass'
    summary = (
        '兩份檔案文字內容一致' if verdict == 'pass' else
        f'發現 {len(differences)} 處差異，請確認後再送印' if verdict == 'fail' else
        f'發現 {len(differences)} 處差異、{len(warnings)} 項待確認'
    )

    return {
        'verdict': verdict,
        'summary': summary,
        'differences': differences,
        'warnings': warnings[:50],
        'confirmed_match': confirmed[:30],
        'notes': f'EPS OCR 共 {len(eps_lines)} 行，PDF OCR 共 {len(pdf_lines)} 行'
    }

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/compare', methods=['POST'])
def compare():
    if 'eps' not in request.files or 'pdf' not in request.files:
        return jsonify({'error': '請上傳 EPS 和 PDF 檔案'}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        eps_file = request.files['eps']
        pdf_file = request.files['pdf']

        eps_path = os.path.join(tmpdir, 'input.eps')
        pdf_path = os.path.join(tmpdir, 'input.pdf')
        eps_file.save(eps_path)
        pdf_file.save(pdf_path)

        try:
            eps_img = eps_to_image(eps_path)
        except Exception as e:
            return jsonify({'error': f'EPS 轉換失敗：{str(e)}'}), 500

        try:
            pdf_img = pdf_to_image(pdf_path)
        except Exception as e:
            return jsonify({'error': f'PDF 轉換失敗：{str(e)}'}), 500

        eps_text = ocr_image(eps_img)
        pdf_text = ocr_image(pdf_img)

        result = diff_texts(eps_text, pdf_text)
        result['eps_text'] = eps_text
        result['pdf_text'] = pdf_text

        return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
