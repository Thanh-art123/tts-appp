import os
import asyncio
from flask import Flask, request, render_template_string, send_file
import edge_tts

app = Flask(__name__)

OUTPUT_FOLDER = 'outputs'
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

VIETNAMESE_VOICES = {
    "vi-VN-HoaiMyNeural": "Tiếng Việt - Nữ Miền Bắc (Hoài Mỹ)",
    "vi-VN-NamMinhNeural": "Tiếng Việt - Nam Miền Bắc (Nam Minh)"
}

# Tự động tạo danh sách tốc độ từ 0.9 đến 2.0 (bước nhảy 0.1)
RATES = []
for i in range(12):
    val = round(0.9 + i * 0.1, 1)
    pct = int((val - 1.0) * 100)
    str_val = f"+{pct}%" if pct >= 0 else f"{pct}%"
    label = f"{val}x" if val != 1.0 else "1.0x (Mặc định)"
    RATES.append({"value": str_val, "label": label})

# Tự động tạo danh sách cao độ từ -200Hz đến +200Hz (bước nhảy 50Hz)
PITCHES = []
for p in range(-200, 201, 50):
    str_val = f"+{p}Hz" if p > 0 else f"{p}Hz"
    label = f"{str_val}" if p != 0 else "0Hz (Mặc định)"
    PITCHES.append({"value": str_val, "label": label})

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chuyển văn bản thành giọng nói AI miễn phí</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 680px; margin: 40px auto; padding: 20px; background-color: #f4f7f6; }
        .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
        h2 { text-align: center; color: #333; margin-bottom: 25px; }
        label { font-weight: bold; display: block; margin-top: 15px; margin-bottom: 5px; color: #555; }
        select, textarea { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; font-size: 14px; }
        textarea { height: 110px; resize: vertical; }
        .row { display: flex; gap: 15px; }
        .col { flex: 1; }
        button { width: 100%; margin-top: 25px; padding: 12px; background-color: #007bff; color: white; border: none; border-radius: 5px; font-size: 16px; font-weight: bold; cursor: pointer; }
        button:hover { background-color: #0056b3; }
        .error { color: #d9534f; background: #fdf7f7; padding: 10px; border-radius: 5px; border: 1px solid #d9534f; margin-bottom: 15px; }
        .audio-player { margin-top: 25px; text-align: center; }
        audio { width: 100%; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Chuyển Văn Bản Thành Giọng Nói AI</h2>
        
        {% if error %}
            <div class="error">Lỗi xử lý: {{ error }}</div>
        {% endif %}

        <form method="POST" action="/">
            <label>1. Chọn chất giọng Tiếng Việt:</label>
            <select name="voice">
                {% for voice_id, voice_name in voices.items() %}
                    <option value="{{ voice_id }}" {% if selected_voice == voice_id %}selected{% endif %}>{{ voice_name }}</option>
                {% endfor %}
            </select>

            <div class="row">
                <div class="col">
                    <label>2. Tốc độ đọc (Rate):</label>
                    <select name="rate">
                        {% for r in rates %}
                            <option value="{{ r.value }}" {% if selected_rate == r.value %}selected{% endif %}>{{ r.label }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="col">
                    <label>3. Cao độ / Độ trầm (Pitch):</label>
                    <select name="pitch">
                        {% for pi in pitches %}
                            <option value="{{ pi.value }}" {% if selected_pitch == pi.value %}selected{% endif %}>{{ pi.label }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>

            <label>4. Nhập văn bản Tiếng Việt cần chuyển đổi:</label>
            <textarea name="text" placeholder="Nhập nội dung văn bản tại đây..." required>{{ text if text else '' }}</textarea>

            <button type="submit">Tạo Âm Thanh Ngay</button>
        </form>

        {% if audio_file %}
            <div class="audio-player">
                <h3>Kết quả âm thanh:</h3>
                <audio controls autoplay src="/download/{{ audio_file }}"></audio>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

async def generate_speech(text, voice, rate, pitch, output_path):
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)

@app.route('/', methods=['GET', 'POST'])
def index():
    error = None
    audio_file = None
    text = ""
    selected_voice = "vi-VN-HoaiMyNeural"
    selected_rate = "+0%"
    selected_pitch = "+0Hz"

    if request.method == 'POST':
        text = request.form.get('text', '')
        selected_voice = request.form.get('voice', 'vi-VN-HoaiMyNeural')
        selected_rate = request.form.get('rate', '+0%')
        selected_pitch = request.form.get('pitch', '+0Hz')

        if not text:
            error = "Vui lòng nhập nội dung văn bản."
            return render_template_string(HTML_TEMPLATE, error=error, text=text, voices=VIETNAMESE_VOICES, rates=RATES, pitches=PITCHES, selected_voice=selected_voice, selected_rate=selected_rate, selected_pitch=selected_pitch)

        output_filename = "speech_custom_pitch.mp3"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        try:
            asyncio.run(generate_speech(text, selected_voice, selected_rate, selected_pitch, output_path))
            audio_file = output_filename
        except Exception as e:
            error = f"Lỗi tạo âm thanh: {str(e)}"

    return render_template_string(HTML_TEMPLATE, error=error, audio_file=audio_file, text=text, voices=VIETNAMESE_VOICES, rates=RATES, pitches=PITCHES, selected_voice=selected_voice, selected_rate=selected_rate, selected_pitch=selected_pitch)

@app.route('/download/<filename>')
def download(filename):
    return send_file(os.path.join(OUTPUT_FOLDER, filename), mimetype="audio/mpeg")

from flask import make_response


@app.route("/sitemap.xml")
def sitemap():
  host = 'https://chuyen-van-ban-thanh-giong-doc.onrender.com'

  xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>{host}/</loc>
            <changefreq>daily</changefreq>
            <priority>1.0</priority>
        </url>
    </urlset>
    """
  response = make_response(xml_content)
  response.headers['Content-Type'] = 'application/xml'
  return response

# Xác minh Google Search Console
@app.route('/google7cf85b3b17783f9c.html')
def google_verify():
  return 'google-site-verification: google7cf85b3b17783f9c.html'

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)