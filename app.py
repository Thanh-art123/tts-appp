import os
import asyncio
from flask import Flask, render_template, request, send_file, jsonify
import edge_tts

app = Flask(__name__)

# Route hiển thị trang chủ
@app.route('/')
def home():
    return render_template('index.html')

# Route xử lý tạo giọng đọc TTS
@app.route('/tts', methods=['POST'])
def generate_tts():
    try:
        text = request.form.get('text')
        rate = request.form.get('rate', '0')
        pitch = request.form.get('pitch', '0')

        if not text:
            return "Vui lòng nhập nội dung văn bản!", 400

        # Định dạng tham số rate và pitch theo chuẩn edge-tts (ví dụ: +10% hoặc -5Hz)
        rate_str = f"{int(rate):+d}%"
        pitch_str = f"{int(pitch):+d}Hz"

        # Tên file âm thanh tạm
        output_file = "output.mp3"

        # Giọng đọc tiếng Việt (vi-VN-HoaiMyNeural hoặc vi-VN-NamMinhNeural)
        voice = "vi-VN-HoaiMyNeural"

        # Hàm bất đồng bộ gọi edge-tts
        async def main():
            communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
            await communicate.save(output_file)

        # Chạy hàm async trong Flask
        asyncio.run(main())

        # Trả file mp3 về cho trình duyệt phát âm thanh
        return send_file(output_file, mimetype="audio/mpeg")

    except Exception as e:
        return f"Lỗi khởi tạo giọng đọc: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
