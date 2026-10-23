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
            
            # Đảm bảo người dùng luôn có đủ cấu trúc tu luyện
            for user in data.get("users", []):
                if "tu_luyen" not in user:
                    user["tu_luyen"] = get_default_tu_luyen()
            return data
    except Exception:
        return initial_data

def save_database(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- CÁC ROUTE GIAO DIỆN ĐIỀU HƯỚNG CHÍNH ---
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

@app.route('/doc-truyen', methods=['POST'])
def doc_truyen():
    title = request.form.get('title', 'Chương Truyện')
    content = request.form.get('content', 'Không có nội dung.')
    return render_template('doc_truyen.html', chapter_title=title, chapter_content=content)

# --- API XÁC THỰC & ĐĂNG NHẬP ---
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
        "role": "member",
        "author_name": author_name,
        "tu_luyen": get_default_tu_luyen()
    }
    db['users'].append(new_user)
    save_database(db)

    return jsonify({"success": True, "message": "Đăng ký thành công!", "user": new_user})

# --- API TU LUYỆN & BÍ CẢNH ---
@app.route('/api/get-tu-luyen', methods=['GET'])
def get_tu_luyen():
    username = request.args.get('username', '').strip()
    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)

    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để xem tiến trình tu luyện!"})

    tl = user.get('tu_luyen', get_default_tu_luyen())
    n = tl["cap_hien_tai"]
    tieu_canh_idx = tl["tieu_canh"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10

    return jsonify({
        "success": True,
        "data": tl,
        "ten_canh": TEN_CANH_GIOI[n-1],
        "ten_tieu_canh": TEN_TIEU_CANH[tieu_canh_idx],
        "diem_max": diem_can_thiet,
        "author_name": user["author_name"]
    })

# --- API QUẢN LÝ KHO TÀNG & ĐỘNG PHỦ ---
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

@app.route('/api/delete-book', methods=['POST'])
def delete_book():
    data = request.json or {}
    book_id = data.get('id')
    username = data.get('username')

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)

    if not user or user.get('role') != 'admin':
        return jsonify({"success": False, "message": "Chỉ có Chưởng Môn Nhân mới có quyền xóa tài nguyên tông môn!"})

    db['books'] = [b for b in db['books'] if b['id'] != book_id]
    save_database(db)
    return jsonify({"success": True, "message": "Đã xóa tài nguyên thành công!"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
