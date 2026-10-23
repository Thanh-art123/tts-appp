import os
import json
import random
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Tên file lưu trữ vĩnh viễn trên server
DB_FILE = "truyen_data.json"

# Hàm đọc dữ liệu từ file JSON (nếu chưa có thì tạo file mặc định)
def load_database():
    if not os.path.exists(DB_FILE):
        initial_data = [
            {
                "id": 1,
                "category": "truyen-dich",
                "element": "Kim",
                "author": "Bán Tiệt Bạch Thái",
                "title": "Lá Xanh",
                "content": "Đêm nhận được giấy báo nhập học, Hạ Diệp chạy tót lên sân thượng ngồi thẫn thờ..."
            }
        ]
        save_database(initial_data)
        return initial_data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

# Hàm ghi dữ liệu vĩnh viễn vào file JSON
def save_database(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Lưu trữ dữ liệu tu luyện giả lập
tu_luyen_data = {
    "diem_tich_tuyem": 120,
    "cap_hien_tai": 1, 
    "tieu_canh": 0,    
    "vat_pham": {
        "hoa": 2,      
        "lua": 1,      
        "cay": 3       
    }
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
    
    return render_template('bi_canh.html', 
                           data=tu_luyen_data, 
                           ten_canh=TEN_CANH_GIOI[n-1], 
                           ten_tieu_canh=TEN_TIEU_CANH[tieu_canh_idx],
                           diem_max=diem_can_thiet)

@app.route('/doc-truyen', methods=['POST'])
def doc_truyen():
    title = request.form.get('title', 'Chương Truyện')
    content = request.form.get('content', 'Không có nội dung.')
    return render_template('doc_truyen.html', chapter_title=title, chapter_content=content)

# API LẤY DANH SÁCH TRUYỆN THEO CHUYÊN MỤC VÀ THUỘC TÍNH
@app.route('/api/get-books', methods=['GET'])
def get_books():
    cat = request.args.get('category', 'truyen-dich')
    elem = request.args.get('element', 'Kim')
    
    database = load_database()
    filtered_books = [
        b for b in database 
        if b.get('category') == cat and b.get('element') == elem
    ]
    return jsonify({"success": True, "books": filtered_books})

# API LẤY TOÀN BỘ TÀI NGUYÊN CHO TRANG ĐỘNG PHỦ
@app.route('/api/get-all-books', methods=['GET'])
def get_all_books():
    database = load_database()
    return jsonify({"success": True, "books": database})

# API TẢI TRUYỆN LÊN SERVER (LƯU VĨNH VIỄN VÀO FILE JSON)
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

    database = load_database()
    
    new_book = {
        "id": len(database) + 1,
        "category": cat,
        "element": elem,
        "author": author,
        "title": title,
        "content": content
    }
    
    database.append(new_book)
    save_database(database) # Lưu thẳng vào ổ cứng vĩnh viễn
    
    # Cộng điểm tu luyện cho tác giả khi đăng bài
    tu_luyen_data["diem_tich_tuyem"] += 50

    return jsonify({"success": True, "message": f"Truyện [{title}] đã được truyền thừa và lưu vĩnh viễn thành công!", "book": new_book})

@app.route('/api/hanh-dong', methods=['POST'])
def hanh_dong():
    req = request.json or {}
    loai = req.get('loai')
    
    if loai == 'dang_bai':
        tu_luyen_data["diem_tich_tuyem"] += 50
    elif loai == 'nghe_truyen':
        tu_luyen_data["diem_tich_tuyem"] += 30
    elif loai == 'nhat_vat_pham':
        tu_luyen_data["diem_tich_tuyem"] += 20
        vp = random.choice(['hoa', 'lua', 'cay'])
        tu_luyen_data["vat_pham"][vp] += 1
        
    return jsonify({"success": True, "data": tu_luyen_data})

@app.route('/api/dot-pha', methods=['POST'])
def dot_pha():
    n = tu_luyen_data["cap_hien_tai"]
    tieu_canh_idx = tu_luyen_data["tieu_canh"]
    diem_hien_tai = tu_luyen_data["diem_tich_tuyem"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10
    
    if diem_hien_tai < diem_can_thiet:
        return jsonify({"success": False, "message": "Không đủ điểm tu luyện để đột phá!"})
        
    if tieu_canh_idx == 3:
        if tu_luyen_data["vat_pham"]["hoa"] < 1 or tu_luyen_data["vat_pham"]["lua"] < 1:
            return jsonify({"success": False, "message": "Đột phá Đại Cảnh Giới yêu cầu phải có ít nhất 1 Hoa và 1 Lửa thần!"})
        
        tu_luyen_data["vat_pham"]["hoa"] -= 1
        tu_luyen_data["vat_pham"]["lua"] -= 1
        
        if n < 12:
            tu_luyen_data["cap_hien_tai"] += 1
            tu_luyen_data["tieu_canh"] = 0
            tu_luyen_data["diem_tich_tuyem"] -= diem_can_thiet
        else:
            return jsonify({"success": False, "message": "Đạo hữu đã đạt đến đỉnh phong Hỗn Độn Chúa Tể, vô địch thiên địa rồi!"})
    else:
        tu_luyen_data["tieu_canh"] += 1
        tu_luyen_data["diem_tich_tuyem"] -= diem_can_thiet
        
    return jsonify({"success": True, "message": "Đột phá thành công!", "data": tu_luyen_data})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
