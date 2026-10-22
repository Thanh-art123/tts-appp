import os
import asyncio
import edge_tts
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Thư mục chứa audio đầu ra
AUDIO_DIR = os.path.join(app.static_folder, 'audio')
os.makedirs(AUDIO_DIR, exist_ok=True)

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
def bi-canh():
    return render_template('bi_canh.html')

@app.route('/tts', methods=['POST'])
def text_to_speech():
    try:
        data = request.json or {}
        text = data.get('text', '')
        voice = data.get('voice', 'vi-VN-HoaiMyNeural')
        rate = data.get('rate', '+0%')
        pitch = data.get('pitch', '+0Hz')

        if not text:
            return jsonify({'error': 'Không có văn bản'}), 400

        # Tiền xử lý chèn nhịp đọc cổ phong
        words = text.split()
        processed_text = ""
        for i, word in enumerate(words):
            processed_text += word + " "
            if (i + 1) % 13 == 0:
                processed_text += " ... "

        output_filename = "speech_output.mp3"
        output_path = os.path.join(AUDIO_DIR, output_filename)

        async def generate_speech():
            communicate = edge_tts.Communicate(processed_text, voice, rate=rate, pitch=pitch)
            await communicate.save(output_path)

        asyncio.run(generate_speech())

        return jsonify({
            'success': True, 
            'audio_url': f'/static/audio/{output_filename}?t={os.path.getmtime(output_path)}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
