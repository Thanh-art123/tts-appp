import os
import asyncio
from flask import Flask, render_template, request, send_file
import edge_tts

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/tts', methods=['POST'])
def generate_tts():
    try:
        text = request.form.get('text')
        # Lấy tham số voice gửi từ giao diện, mặc định nếu trống mới lấy HoaiMy
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
