import os
import json
import random
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "tong_mon_secret_key_chuong_mon_toi_cao"

DB_FILE = "tong_mon_data.json"

# Khởi tạo cơ sở dữ liệu mặc định nếu chưa có
def load_database():
    if not os.path.exists(DB_FILE):
        initial_data = {
            "users": [
                {
                    "username": "admin",
                    "password": "123", # Mật khẩu mặc định của Chưởng Môn, có thể đổi
                    "role": "admin",    # Chưởng Môn tối cao
                    "author_name": "Chưởng Môn Nhân"
                }
            ],
            "books": [
                {
                    "id": 1,
                    "category": "truyen-dich",
                    "element": "Kim",
                    "author": "Chưởng Môn Nhân",
                    "title": "Lá Xanh (Kinh Điển Tông Môn)",
                    "content": "Đêm nhận được giấy báo nhập học, Hạ Diệp chạy tót lên sân thượng ngồi thẫn thờ..."
                }
            ]
        }
        save_database(initial_data)
        return initial_data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": [], "books": []}

def save_database(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Lưu trữ dữ liệu tu luyện giả lập
tu_luyen_data = {
    "diem_tich_tuyem": 120,
    "cap_hien_tai": 1, 
    "tieu_canh": 0,    
    "vat_pham": { "hoa": 2, "lua": 1, "cay": 3 }
}

TEN_CANH_GIOI = [
    "Phàm Nhân", "Luyện Khí", "Trúc Cơ", "Kim Đan", 
    "Nguyên Anh", "Hóa Thần", "Luyện Hư", "Hợp Thể", 
    "Độ Kiếp", "Chân Tiên", "Tiên Vương", "Hỗn Độn Chúa Tể"
]
TEN_TIEU_CANH = ["Nhập Môn", "Tiểu Thành", "Đại Thành", "Viên Mãn"]

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
    n = tu_luyen_data["cap_hien_tai"]
    tieu_canh_idx = tu_luyen_data["tieu_canh"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10
    return render_template('bi_canh.html', data=tu_luyen_data, ten_canh=TEN_CANH_GIOI[n-1], ten_tieu_canh=TEN_TIEU_CANH[tieu_canh_idx], diem_max=diem_can_thiet)

@app.route('/doc-truyen', methods=['POST'])
def doc_truyen():
    title = request.form.get('title', 'Chương Truyện')
    content = request.form.get('content', 'Không có nội dung.')
    return render_template('doc_truyen.html', chapter_title=title, chapter_content=content)

# --- API XÁC THỰC & ĐĂNG NHẬP ĐA THIẾT BỊ ---
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username and u['password'] == password), None)

    if user:
        return jsonify({"success": True, "message": f"Đăng nhập thành công với danh nghĩa [{user['author_name']}]!", "user": user})
    else:
        return jsonify({"success": False, "message": "Sai tài khoản hoặc mật khẩu!"})

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    author_name = data.get('author_name', '').strip()

    if not username or not password or not author_name:
        return jsonify({"success": False, "message": "Vui lòng điền đầy đủ thông tin!"})

    db = load_database()
    if any(u['username'] == username for u in db['users']):
        return jsonify({"success": False, "message": "Tài khoản (Đạo hiệu) này đã tồn tại!"})

    new_user = {
        "username": username,
        "password": password,
        "role": "member", # Đệ tử thông thường
        "author_name": author_name
    }
    db['users'].append(new_user)
    save_database(db)

    return jsonify({"success": True, "message": "Đăng ký thành công!", "user": new_user})

# --- API QUẢN LÝ KHO TÀNG ---
@app.route('/api/get-books', methods=['GET'])
def get_books():
    cat = request.args.get('category', 'truyen-dich')
    elem = request.args.get('element', 'Kim')
    db = load_database()
    filtered = [b for b in db['books'] if b.get('category') == cat and b.get('element') == elem]
    return jsonify({"success": True, "books": filtered})

@app.route('/api/get-all-books', methods=['GET'])
def get_all_books():
    db = load_database()
    return jsonify({"success": True, "books": db['books']})

@app.route('/api/upload-book', methods=['POST'])
def upload_book():
    data = request.json or {}
    title = data.get('title')
    content = data.get('content')
    author = data.get('author', 'Vô Danh')
    cat = data.get('category', 'truyen-dich')
    elem = data.get('element', 'Kim')

    if not title or not content:
        return jsonify({"success": False, "message": "Thiếu thông tin bộ truyện!"})

    db = load_database()
    new_book = {
        "id": len(db['books']) + 1,
        "category": cat,
        "element": elem,
        "author": author,
        "title": title,
        "content": content
    }
    db['books'].append(new_book)
    save_database(db)

    tu_luyen_data["diem_tich_tuyem"] += 50
    return jsonify({"success": True, "message": f"Truyện [{title}] đã được truyền thừa vĩnh viễn trên Cloud!", "book": new_book})

# API CHƯỞNG MÔN XÓA TÀI NGUYÊN (QUYỀN TỐI CAO)
@app.route('/api/delete-book', methods=['POST'])
def delete_book():
    data = request.json or {}
    book_id = data.get('id')
    username = data.get('username')

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)

    # Kiểm tra xem có phải Admin (Chưởng Môn) không
    if not user or user.get('role') != 'admin':
        return jsonify({"success": False, "message": "Chỉ có Chưởng Môn Nhân mới có quyền xóa tài nguyên tông môn!"})

    db['books'] = [b for b in db['books'] if b['id'] != book_id]
    save_database(db)
    return jsonify({"success": True, "message": "Đã xóa tài nguyên thành công!"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
