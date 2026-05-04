import os
import subprocess
import tempfile
from flask import Flask, request, jsonify
from flask_cors import CORS
import fitz  # PyMuPDF - can do OCR itself with its own engine

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

def eps_to_image(eps_path):
    """Convert EPS to PNG using Ghostscript"""
    out_path = eps_path + '.png'
    result = subprocess.run([
        'gs', '-dNOPAUSE', '-dBATCH', '-dSAFER',
        '-sDEVICE=png16m', '-r150',
        '-sOutputFile=' + out_path,
        eps_path
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception('Ghostscript error: ' + result.stderr[:200])
    return out_path

def pdf_to_text(pdf_path):
    """Extract text directly from PDF using PyMuPDF"""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def pdf_to_image_text(pdf_path):
    """Render PDF page and extract text blocks"""
    doc = fitz.open(pdf_path)
    page = doc[0]
    # Get text with layout
    blocks = page.get_text("blocks")
    lines = []
    for b in blocks:
        t = b[4].strip()
        if t:
            lines.extend(t.split('\n'))
    doc.close()
    return '\n'.join(l.strip() for l in lines if l.strip())

def eps_to_text_via_gs(eps_path):
    """Extract text from EPS using Ghostscript ps2ascii"""
    result = subprocess.run(
        ['gs', '-dNOPAUSE', '-dBATCH', '-dSAFER', '-sDEVICE=txtwrite',
         '-sOutputFile=-', eps_path],
        capture_output=True, text=True, errors='replace'
    )
    return result.stdout if result.stdout.strip() else ""

def diff_texts(eps_text, pdf_text):
    def clean(t):
        return [l.strip() for l in t.split('\n') if len(l.strip()) > 2]
    eps_lines = clean(eps_text)
    pdf_lines = clean(pdf_text)

    def similar(a, b):
        if a == b: return 1.0
        if a in b or b in a: return 0.9
        sa, sb = set(a), set(b)
        if not sa or not sb: return 0
        return len(sa & sb) / len(sa | sb)

    differences, warnings, confirmed = [], [], []

    for line in pdf_lines:
        best = max(eps_lines, key=lambda el: similar(el, line), default=None)
        if best and similar(best, line) > 0.75:
            if best == line:
                confirmed.append({'field': '文字行', 'value': line[:80]})
            else:
                differences.append({'field': '文字差異', 'pdf_value': line[:80], 'eps_value': best[:80], 'severity': 'medium'})
        else:
            warnings.append({'field': 'PDF 有，EPS 未確認', 'reason': line[:80], 'suggestion': '請人工對照 EPS 確認'})

    for line in eps_lines:
        best = max(pdf_lines, key=lambda pl: similar(pl, line), default=None)
        if not best or similar(best, line) <= 0.75:
            differences.append({'field': 'EPS 獨有文字', 'eps_value': line[:80], 'pdf_value': '（PDF 中未找到）', 'severity': 'high'})

    high = len([d for d in differences if d['severity'] == 'high'])
    verdict = 'fail' if high > 0 else 'warn' if differences or warnings else 'pass'
    summary = (
        '兩份檔案文字內容一致' if verdict == 'pass' else
        f'發現 {len(differences)} 處差異，請確認後再送印' if verdict == 'fail' else
        f'發現 {len(differences)} 處差異、{len(warnings)} 項待確認'
    )
    return {
        'verdict': verdict, 'summary': summary,
        'differences': differences, 'warnings': warnings[:50],
        'confirmed_match': confirmed[:30],
        'notes': f'EPS 文字 {len(eps_lines)} 行，PDF 文字 {len(pdf_lines)} 行',
        'eps_text': eps_text[:2000], 'pdf_text': pdf_text[:2000]
    }

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/compare', methods=['POST', 'OPTIONS'])
def compare():
    if request.method == 'OPTIONS':
        return '', 204
    if 'eps' not in request.files or 'pdf' not in request.files:
        return jsonify({'error': '請上傳 EPS 和 PDF 檔案'}), 400
    with tempfile.TemporaryDirectory() as tmpdir:
        eps_path = os.path.join(tmpdir, 'input.eps')
        pdf_path = os.path.join(tmpdir, 'input.pdf')
        request.files['eps'].save(eps_path)
        request.files['pdf'].save(pdf_path)

        # Extract text from PDF directly (no OCR needed)
        try:
            pdf_text = pdf_to_image_text(pdf_path)
        except Exception as e:
            return jsonify({'error': f'PDF 讀取失敗：{str(e)}'}), 500

        # Extract text from EPS using Ghostscript txtwrite
        try:
            eps_text = eps_to_text_via_gs(eps_path)
            if not eps_text.strip():
                # fallback: try ps2ascii approach
                result = subprocess.run(
                    ['gs', '-q', '-dNOPAUSE', '-dBATCH', '-dSAFER',
                     '-sDEVICE=txtwrite', '-dTextFormat=3',
                     '-sOutputFile=-', eps_path],
                    capture_output=True, text=True, errors='replace'
                )
                eps_text = result.stdout
        except Exception as e:
            return jsonify({'error': f'EPS 讀取失敗：{str(e)}'}), 500

        return jsonify(diff_texts(eps_text, pdf_text))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
