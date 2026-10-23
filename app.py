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

# Khởi tạo & nạp cơ sở dữ liệu BẢO VỆ VĨNH VIỄN - Không bao giờ ghi đè file cũ
def load_database():
    initial_data = {
        "users": [
            {
                "username": "admin",
                "password": "123",
                "role": "admin",
                "author_name": "Chưởng Môn Nhân",
                "tu_luyen": {
                    "diem_tich_tuyem": 120,
                    "cap_hien_tai": 1, 
                    "tieu_canh": 0,    
                    "vat_pham": { "hoa": 2, "lua": 1, "cay": 3 }
                }
            }
        ],
        "books": [
            {
                "id": 1,
                "category": "truyen-dich",
                "element": "Kim",
                "author": "Chưởng Môn Nhân",
                "username": "admin",
                "icon": "📜",
                "title": "Lá Xanh (Kinh Điển Tông Môn)",
                "content": "Chương 1: Khai văn đại cát\nĐêm nhận được giấy báo nhập học, Hạ Diệp chạy tót lên sân thượng ngồi thẫn thờ..."
            }
        ]
    }

    # Nếu file chưa tồn tại lần đầu tiên mới khởi tạo
    if not os.path.exists(DB_FILE):
        save_database(initial_data)
        return initial_data
    
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "books" not in data or "users" not in data:
                return initial_data
            
            # Đảm bảo cấu trúc tài khoản tu luyện không bị thiếu
            for user in data.get("users", []):
                if "tu_luyen" not in user:
                    user["tu_luyen"] = get_default_tu_luyen()
            return data
    except Exception:
        return initial_data

def save_database(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

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
        "role": "member",
        "author_name": author_name,
        "tu_luyen": get_default_tu_luyen()
    }
    db['users'].append(new_user)
    save_database(db)

    return jsonify({"success": True, "message": "Đăng ký thành công!", "user": new_user})

# --- API QUẢN LÝ BÍ CẢNH & TU LUYỆN CÁ NHÂN ---
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

@app.route('/api/hanh-dong', methods=['POST'])
def hanh_dong():
    req = request.json or {}
    username = req.get('username', '').strip()
    loai = req.get('loai')

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)

    if not user:
        return jsonify({"success": False, "message": "Vui lòng đăng nhập tài khoản trước!"})

    tl = user.get('tu_luyen', get_default_tu_luyen())

    if loai == 'dang_bai':
        tl["diem_tich_tuyem"] += 50
    elif loai == 'nghe_truyen':
        tl["diem_tich_tuyem"] += 30
    elif loai == 'nhat_vat_pham':
        tl["diem_tich_tuyem"] += 20
        vp = random.choice(['hoa', 'lua', 'cay'])
        tl["vat_pham"][vp] += 1

    user['tu_luyen'] = tl
    save_database(db)

    n = tl["cap_hien_tai"]
    tieu_canh_idx = tl["tieu_canh"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10

    return jsonify({
        "success": True,
        "data": tl,
        "ten_canh": TEN_CANH_GIOI[n-1],
        "ten_tieu_canh": TEN_TIEU_CANH[tieu_canh_idx],
        "diem_max": diem_can_thiet
    })

@app.route('/api/dot-pha', methods=['POST'])
def dot_pha():
    req = request.json or {}
    username = req.get('username', '').strip()

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)

    if not user:
        return jsonify({"success": False, "message": "Vui lòng đăng nhập trước khi đột phá!"})

    tl = user.get('tu_luyen', get_default_tu_luyen())
    n = tl["cap_hien_tai"]
    tieu_canh_idx = tl["tieu_canh"]
    diem_hien_tai = tl["diem_tich_tuyem"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10

    if diem_hien_tai < diem_can_thiet:
        return jsonify({"success": False, "message": "Không đủ điểm tu luyện để đột phá!"})

    if tieu_canh_idx == 3:
        if tl["vat_pham"]["hoa"] < 1 or tl["vat_pham"]["lua"] < 1:
            return jsonify({"success": False, "message": "Đột phá Đại Cảnh Giới yêu cầu phải có ít nhất 1 Hoa và 1 Lửa thần!"})
        
        tl["vat_pham"]["hoa"] -= 1
        tl["vat_pham"]["lua"] -= 1
        
        if n < 12:
            tl["cap_hien_tai"] += 1
            tl["tieu_canh"] = 0
            tl["diem_tich_tuyem"] -= diem_can_thiet
        else:
            return jsonify({"success": False, "message": "Đạo hữu đã đạt đến đỉnh phong Hỗn Độn Chúa Tể, vô địch thiên địa rồi!"})
    else:
        tl["tieu_canh"] += 1
        tl["diem_tich_tuyem"] -= diem_can_thiet

    user['tu_luyen'] = tl
    save_database(db)

    new_n = tl["cap_hien_tai"]
    new_tieu_idx = tl["tieu_canh"]
    new_diem_max = 100 * new_n + (new_tieu_idx + 1) * 50 + (new_n ** 2) * 10

    return jsonify({
        "success": True,
        "message": "Đột phá thành công!",
        "data": tl,
        "ten_canh": TEN_CANH_GIOI[new_n-1],
        "ten_tieu_canh": TEN_TIEU_CANH[new_tieu_idx],
        "diem_max": new_diem_max
    })

# --- API QUẢN LÝ KHO TÀNG & TÀI NGUYÊN ĐỘNG PHỦ ---
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

# API lấy danh sách tài nguyên do chính đạo hữu đã cống hiến (Hiển thị tại Động Phủ)
@app.route('/api/get-user-contributions', methods=['GET'])
def get_user_contributions():
    username = request.args.get('username', '').strip()
    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username), None)
    
    if not user:
        return jsonify({"success": False, "message": "Vui lòng đăng nhập!"})
    
    # Lọc sách theo username hoặc author_name của tài khoản
    user_books = [
        b for b in db['books'] 
        if b.get('username') == username or b.get('author') == user.get('author_name')
    ]
    
    return jsonify({
        "success": True, 
        "books": user_books,
        "author_name": user.get('author_name')
    })

@app.route('/api/upload-book', methods=['POST'])
def upload_book():
    data = request.json or {}
    title = data.get('title')
    content = data.get('content')
    author = data.get('author', 'Vô Danh')
    username = data.get('username', '').strip()
    cat = data.get('category', 'truyen-dich')
    elem = data.get('element', 'Kim')

    if not title or not content:
        return jsonify({"success": False, "message": "Thiếu thông tin bộ truyện!"})

    db = load_database()
    
    # Tạo ID duy nhất
    new_id = max([b.get('id', 0) for b in db['books']], default=0) + 1
    
    new_book = {
        "id": new_id,
        "category": cat,
        "element": elem,
        "author": author,
        "username": username,
        "icon": "📜" if cat == "truyen-dich" else "📖",
        "title": title,
        "content": content
    }
    db['books'].append(new_book)

    # Tăng điểm tu luyện cho tài khoản đăng bài
    user = next((u for u in db['users'] if u['username'] == username), None)
    if user:
        user.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50

    save_database(db)

    return jsonify({"success": True, "message": f"Truyện [{title}] đã được cống hiến vĩnh viễn vào Tàng Kinh Các & Động Phủ!", "book": new_book})

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
