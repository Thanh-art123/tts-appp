import os
import json
import random
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.secret_key = "tong_mon_secret_key_chuong_mon_toi_cao"

DB_FILE = "tong_mon_data.json"

TEN_CANH_GIOI = [
    "Phàm Nhân", "Luyện Khí", "Trúc Cơ", "Kim Đan", 
    "Nguyên Anh", "Hóa Thần", "Luyện Hư", "Hợp Thể", 
    "Độ Kiếp", "Chân Tiên", "Tiên Vương", "Hỗn Độn Chúa Tể"
]
TEN_TIEU_CANH = ["Nhập Môn", "Tiểu Thành", "Đại Thành", "Viên Mãn"]

def get_default_tu_luyen():
    return {
        "diem_tich_tuyem": 120,
        "cap_hien_tai": 1, 
        "tieu_canh": 0,    
        "vat_pham": { "hoa": 2, "lua": 1, "cay": 3 }
    }

# HÀM NẠP DATABASE BẢO VỆ TUYỆT ĐỐI - KHÔNG BAO GIỜ GHI ĐÈ FILE CŨ NẾU ĐÃ TỒN TẠI
def load_database():
    initial_data = {
        "users": [
            {
                "username": "admin",
                "password": "123",
                "role": "admin",
                "author_name": "Chưởng Môn Nhân",
                "tu_luyen": get_default_tu_luyen()
            }
        ],
        "books": []
    }

    # CHỈ TẠO MỚI KHI FILE HOÀN TOÀN KHÔNG TỒN TẠI TRÊN Ổ CỨNG
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=4)
        return initial_data
    
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "books" not in data or "users" not in data:
                return initial_data
            return data
    except Exception:
        return initial_data

def save_database(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- API ĐỘNG PHỦ LẤY TÀI NGUYÊN CỦA ĐẠO HỮU ---
@app.route('/api/get-user-contributions', methods=['GET'])
def get_user_contributions():
    username = request.args.get('username', '').strip()
    db = load_database()
    
    # Lọc tất cả sách do đúng username này đăng lên
    user_books = [b for b in db['books'] if b.get('username') == username]
    
    return jsonify({
        "success": True, 
        "books": user_books
    })

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
    username = data.get('username', 'admin').strip()
    cat = data.get('category', 'truyen-dich')
    elem = data.get('element', 'Kim')

    if not title or not content:
        return jsonify({"success": False, "message": "Thiếu thông tin bộ truyện!"})

    db = load_database()
    new_id = max([b.get('id', 0) for b in db['books']], default=0) + 1
    
    new_book = {
        "id": new_id,
        "category": cat,
        "element": elem,
        "author": author,
        "username": username, # Lưu vết vĩnh viễn username của người đăng
        "icon": "📜" if cat == "truyen-dich" else "📖",
        "title": title,
        "content": content
    }
    db['books'].append(new_book)

    # Tăng điểm tu luyện
    user = next((u for u in db['users'] if u['username'] == username), None)
    if user:
        user.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50

    save_database(db)
    return jsonify({"success": True, "message": f"Truyện [{title}] đã được lưu vào Động Phủ và Tàng Kinh Các vĩnh viễn!", "book": new_book})
