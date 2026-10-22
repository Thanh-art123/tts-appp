import os
import re
import asyncio
from flask import Flask, render_template, request, send_file
import edge_tts

app = Flask(__name__)

def add_xianxia_pauses(text):
    """
    Tự động ngắt nhịp tu tiên thông minh: cứ 12 - 15 từ sẽ chèn khoảng nghỉ
    giúp thính giả nghe cuốn hút và đúng nhịp độ.
    """
    lines = text.split('\n')
    processed_lines = []

    for line in lines:
        words = line.split()
        if not words:
            processed_lines.append('')
            continue
        
        # Nhóm 13 từ thành 1 cụm ngắt nhịp
        chunk_size = 13
        chunks = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
        
        # Nối các cụm từ lại bằng khoảng dừng tự nhiên
        line_processed = " ... ".join(chunks)
        processed_lines.append(line_processed)

    return " ... ... \n".join(processed_lines)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tang-kinh-cac')
def tang_kinh_cac():
    return render_template('tang_kinh_cac.html')

@app.route('/che-tap-cac')
def che_tap_cac():
    return render_template('che_tap_cac.html')

@app.route('/dong-phu')
def dong_phu():
    return render_template('dong_phu.html')

@app.route('/bi-canh')
def bi_canh():
    return render_template('bi_canh.html')

@app.route('/tts', methods=['POST'])
def generate_tts():
    try:
        raw_text = request.form.get('text')
        voice = request.form.get('voice') or "vi-VN-HoaiMyNeural"
        rate = request.form.get('rate', '0')
        pitch = request.form.get('pitch', '0')

        if not raw_text:
            return "Vui lòng nhập nội dung văn bản!", 400

        # Áp dụng khuôn mẫu ngắt nhịp tu tiên cuốn hút
        formatted_text = add_xianxia_pauses(raw_text)

        rate_str = f"{int(rate):+d}%"
        pitch_str = f"{int(pitch):+d}Hz"
        output_file = "output.mp3"

        async def main():
            communicate = edge_tts.Communicate(formatted_text, voice, rate=rate_str, pitch=pitch_str)
            await communicate.save(output_file)

        asyncio.run(main())
        return send_file(output_file, mimetype="audio/mpeg")

    except Exception as e:
        return f"Lỗi khởi tạo giọng đọc: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
