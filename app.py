import os
import asyncio
from flask import Flask, render_template, request, send_file
import edge_tts

app = Flask(__name__)

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
        text = request.form.get('text')
        voice = request.form.get('voice') or "vi-VN-HoaiMyNeural"
        rate = request.form.get('rate', '0')
        pitch = request.form.get('pitch', '0')

        if not text:
            return "Vui lòng nhập nội dung văn bản!", 400

        rate_str = f"{int(rate):+d}%"
        pitch_str = f"{int(pitch):+d}Hz"
        output_file = "output.mp3"

        async def main():
            communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
            await communicate.save(output_file)

        asyncio.run(main())
        return send_file(output_file, mimetype="audio/mpeg")

    except Exception as e:
        return f"Lỗi khởi tạo giọng đọc: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
