import os
from flask import Flask, render_template

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
