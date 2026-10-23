import os
import random
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

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

# Kho lưu trữ truyện server-side (Đã có sẵn dữ liệu mẫu Lá Xanh)
truyen_database = [
    {
        "id": 1,
        "category": "truyen-dich",
        "element": "Kim",
        "author": "Bán Tiệt Bạch Thái",
        "title": "Lá Xanh",
        "content": "Đêm nhận được giấy báo nhập học, Hạ Diệp chạy tót lên sân thượng ngồi thẫn thờ..."
    }
]

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
    
    filtered_books = [
        b for b in truyen_database 
        if b['category'] == cat and b['element'] == elem
    ]
    return jsonify({"success": True, "books": filtered_books})

# API TẢI TRUYỆN LÊN SERVER (SẼ LƯU VĨNH VIỄN)
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

    new_book = {
        "id": len(truyen_database) + 1,
        "category": cat,
        "element": elem,
        "author": author,
        "title": title,
        "content": content
    }
    truyen_database.append(new_book)
    
    # Cộng điểm tu luyện cho tác giả khi đăng bài
    tu_luyen_data["diem_tich_tuyem"] += 50

    return jsonify({"success": True, "message": f"Truyện [{title}] đã được truyền thừa thành công!", "book": new_book})

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
