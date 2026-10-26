import os
import json
import base64
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tong_mon_secret_key_chuong_mon_toi_cao")
# Phiên đăng nhập bền 1 năm: đăng nhập 1 lần, những lần sau vào web là vẫn còn đăng nhập
app.permanent_session_lifetime = timedelta(days=365)

DB_FILE = "tong_mon_data.json"

TEN_CANH_GIOI = [
    "Phàm Nhân", "Luyện Khí", "Trúc Cơ", "Kim Đan",
    "Nguyên Anh", "Hóa Thần", "Luyện Hư", "Hợp Thể",
    "Độ Kiếp", "Chân Tiên", "Tiên Vương", "Hỗn Độn Chúa Tể"
]
TEN_TIEU_CANH = ["Nhập Môn", "Tiểu Thành", "Đại Thành", "Viên Mãn"]

# ============================================================
# LỚP LƯU TRỮ BỀN VỮNG TRÊN GITHUB (NHÁNH "data")
# ------------------------------------------------------------
# Ổ đĩa của Render (gói Free) là ổ đĩa tạm: mỗi lần deploy lại
# hoặc server khởi động lại, file tong_mon_data.json trên ổ đĩa
# sẽ bị xóa sạch -> tài khoản và truyện của thính giả mất hết.
#
# Giải pháp: toàn bộ dữ liệu được lưu thêm vào chính repo GitHub
# của web, trên một nhánh riêng tên "data" (không đụng vào code
# ở nhánh main). Sửa code / deploy bao nhiêu lần đi nữa, dữ liệu
# vẫn nằm nguyên trên GitHub. Chỉ cần đăng nhập đúng tên tài
# khoản đã tạo là tài nguyên trong Túi Trữ Vật còn mãi mãi.
#
# Cần cấu hình trên Render (Environment):
#   GITHUB_TOKEN        = Personal Access Token có quyền
#                         Contents: Read and write trên repo này
#   GITHUB_REPO         = Thanh-art123/tts-appp   (mặc định)
#   GITHUB_DATA_BRANCH  = data                    (mặc định)
# Nếu không có GITHUB_TOKEN (chạy thử ở máy cá nhân), app tự
# lùi về lưu file cục bộ như trước đây.
# ============================================================
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Thanh-art123/tts-appp").strip()
GITHUB_DATA_BRANCH = os.environ.get("GITHUB_DATA_BRANCH", "data").strip()
GH_API = "https://api.github.com"

_db_lock = threading.Lock()
_db_cache = {"data": None, "commit": None, "gh_loaded": False}


def _gh_request(method, path, payload=None):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tong-mon-app",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(GH_API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        print(f"[GITHUB] Loi ket noi: {e}")
        return 0, {}


def _gh_branch_head(branch):
    status, res = _gh_request("GET", f"/repos/{GITHUB_REPO}/git/ref/heads/{branch}")
    if status == 200:
        return res.get("object", {}).get("sha")
    return None


def _gh_ensure_data_branch():
    """Tạo nhánh data (từ main) nếu chưa có. Trả True nếu nhánh sẵn sàng."""
    if _gh_branch_head(GITHUB_DATA_BRANCH):
        return True
    main_sha = _gh_branch_head("main")
    if not main_sha:
        return False
    status, _ = _gh_request("POST", f"/repos/{GITHUB_REPO}/git/refs",
                            {"ref": f"refs/heads/{GITHUB_DATA_BRANCH}", "sha": main_sha})
    return status in (200, 201, 422)  # 422 = nhánh đã tồn tại


def _gh_read_db():
    """
    Đọc file dữ liệu trên nhánh data bằng Git Data API (hỗ trợ file lớn).
    Trả về (data | None, commit_sha | None, ok):
      ok=True  và data=None  -> nhánh có nhưng chưa có file dữ liệu (lần đầu)
      ok=False               -> lỗi API/mạng, không được ghi đè lên GitHub
    """
    head = _gh_branch_head(GITHUB_DATA_BRANCH)
    if not head:
        return None, None, False
    status, commit = _gh_request("GET", f"/repos/{GITHUB_REPO}/git/commits/{head}")
    if status != 200:
        return None, head, False
    tree_sha = commit.get("tree", {}).get("sha")
    status, tree = _gh_request("GET", f"/repos/{GITHUB_REPO}/git/trees/{tree_sha}")
    if status != 200:
        return None, head, False
    blob_sha = next((t["sha"] for t in tree.get("tree", []) if t.get("path") == DB_FILE), None)
    if not blob_sha:
        return None, head, True
    status, blob = _gh_request("GET", f"/repos/{GITHUB_REPO}/git/blobs/{blob_sha}")
    if status != 200 or "content" not in blob:
        return None, head, False
    try:
        raw = base64.b64decode(blob["content"]).decode("utf-8")
        return json.loads(raw), head, True
    except Exception:
        return None, head, False


def _gh_write_db(data, message):
    """Ghi dữ liệu lên nhánh data: blob -> tree -> commit -> cập nhật ref. Thử lại 1 lần nếu bị xung đột."""
    for _ in range(2):
        head = _db_cache.get("commit") or _gh_branch_head(GITHUB_DATA_BRANCH)
        if not head:
            return False
        status, commit = _gh_request("GET", f"/repos/{GITHUB_REPO}/git/commits/{head}")
        if status != 200:
            return False
        base_tree = commit.get("tree", {}).get("sha")
        content_b64 = base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")
        status, blob = _gh_request("POST", f"/repos/{GITHUB_REPO}/git/blobs",
                                   {"content": content_b64, "encoding": "base64"})
        if status not in (200, 201):
            return False
        status, tree = _gh_request("POST", f"/repos/{GITHUB_REPO}/git/trees",
                                   {"base_tree": base_tree,
                                    "tree": [{"path": DB_FILE, "mode": "100644",
                                              "type": "blob", "sha": blob["sha"]}]})
        if status not in (200, 201):
            return False
        status, new_commit = _gh_request("POST", f"/repos/{GITHUB_REPO}/git/commits",
                                         {"message": message, "tree": tree["sha"], "parents": [head]})
        if status not in (200, 201):
            return False
        status, _ = _gh_request("PATCH", f"/repos/{GITHUB_REPO}/git/refs/heads/{GITHUB_DATA_BRANCH}",
                                {"sha": new_commit["sha"], "force": False})
        if status == 200:
            _db_cache["commit"] = new_commit["sha"]
            return True
        # Bị xung đột do có commit khác xen vào: nạp lại head rồi thử lại
        _db_cache["commit"] = _gh_branch_head(GITHUB_DATA_BRANCH)
    return False


def get_default_tu_luyen():
    return {
        "diem_tich_tuyem": 120,
        "cap_hien_tai": 1,
        "tieu_canh": 0,
        "vat_pham": {"hoa": 2, "lua": 1, "cay": 3}
    }


def _initial_data():
    return {
        "users": [
            {
                "username": "admin",
                "password": "123",
                "role": "admin",
                "author_name": "Chưởng Môn Nhân",
                "tu_luyen": get_default_tu_luyen(),
                "bag": []
            }
        ],
        "books": []
    }


def _normalize_db(data):
    """Đảm bảo cấu trúc dữ liệu luôn đủ trường (kể cả dữ liệu cũ)."""
    if not isinstance(data, dict):
        return _initial_data()
    data.setdefault("users", [])
    data.setdefault("books", [])
    for user in data["users"]:
        if "tu_luyen" not in user:
            user["tu_luyen"] = get_default_tu_luyen()
        if "bag" not in user or not isinstance(user["bag"], list):
            user["bag"] = []
    return data


def _load_local_file():
    if not os.path.exists(DB_FILE):
        data = _initial_data()
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _initial_data()


def load_database():
    """Nạp dữ liệu: ưu tiên GitHub (bền vững), lùi về file cục bộ khi không có token/lỗi mạng."""
    if _db_cache["data"] is not None:
        return _db_cache["data"]
    with _db_lock:
        if _db_cache["data"] is not None:
            return _db_cache["data"]
        data = None
        if GITHUB_TOKEN:
            try:
                if _gh_ensure_data_branch():
                    gh_data, head, ok = _gh_read_db()
                    if ok:
                        _db_cache["gh_loaded"] = True
                        _db_cache["commit"] = head
                        if gh_data is not None:
                            data = gh_data
            except Exception as e:
                print(f"[GITHUB] Khong nap duoc du lieu tu GitHub: {e}")
        if data is None:
            # Chưa có dữ liệu trên GitHub (lần chạy đầu) hoặc không dùng GitHub:
            # dùng dữ liệu cục bộ / khởi tạo mới.
            data = _load_local_file()
        data = _normalize_db(data)
        _db_cache["data"] = data
        return data


def save_database(data, message="Cap nhat du lieu Tong Mon"):
    """Lưu dữ liệu: ghi bản sao cục bộ + đẩy lên GitHub (nếu đã nạp được từ GitHub)."""
    _db_cache["data"] = data
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
    if GITHUB_TOKEN and _db_cache.get("gh_loaded"):
        if not _gh_write_db(data, message):
            print("[GITHUB] CANH BAO: ghi du lieu len GitHub that bai, se thu lai o lan luu sau.")


def find_user(db, username):
    username = (username or "").strip()
    return next((u for u in db["users"] if u["username"] == username), None)


def public_user(user):
    """Bản sao thông tin user an toàn để trả về trình duyệt (không kèm mật khẩu/túi đồ)."""
    return {
        "username": user["username"],
        "role": user.get("role", "member"),
        "author_name": user.get("author_name", user["username"]),
        "tu_luyen": user.get("tu_luyen", get_default_tu_luyen())
    }


def now_jst():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%d/%m/%Y %H:%M")


# ============================================================
# CỔNG ĐĂNG NHẬP TOÀN WEB
# ------------------------------------------------------------
# Trước khi vào được bất kỳ trang nào của web, người dùng bắt
# buộc phải đăng nhập (hoặc đăng ký tài khoản mới). Thông tin
# tài khoản + mọi tài nguyên người dùng đăng lên được lưu vĩnh
# viễn trên GitHub (nhánh data) nên không bao giờ mất.
# ============================================================
_PUBLIC_PATHS = {"/dang-nhap", "/api/login", "/api/register", "/favicon.ico"}


@app.before_request
def _require_login():
    path = request.path
    if path.startswith("/static/") or path in _PUBLIC_PATHS:
        return None
    if session.get("username"):
        return None
    if path.startswith("/api/"):
        return jsonify({"success": False,
                        "message": "Cần đăng nhập để sử dụng Tông Môn!",
                        "need_login": True}), 401
    return redirect(url_for("dang_nhap"))


@app.route('/dang-nhap')
def dang_nhap():
    if session.get("username"):
        return redirect(url_for('home'))
    return render_template('dang_nhap.html')


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
        session.permanent = True
        session['username'] = user['username']
        return jsonify({"success": True, "message": f"Đăng nhập thành công với danh nghĩa [{user['author_name']}]!", "user": public_user(user)})
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
        "tu_luyen": get_default_tu_luyen(),
        "bag": []
    }
    db['users'].append(new_user)
    save_database(db, f"Dang ky tai khoan moi: {username}")

    # Đăng ký xong là đăng nhập luôn để vào web
    session.permanent = True
    session['username'] = username
    return jsonify({"success": True, "message": "Đăng ký thành công!", "user": public_user(new_user)})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({"success": True, "message": "Đã đăng xuất!"})


@app.route('/api/me', methods=['GET'])
def api_me():
    username = session.get('username')
    if not username:
        return jsonify({"success": False, "message": "Chưa đăng nhập!"})
    db = load_database()
    user = find_user(db, username)
    if not user:
        session.clear()
        return jsonify({"success": False, "message": "Tài khoản không còn tồn tại!"})
    return jsonify({"success": True, "user": public_user(user)})


# --- API TU LUYỆN & BÍ CẢNH ---
@app.route('/api/get-tu-luyen', methods=['GET'])
def get_tu_luyen():
    username = request.args.get('username', '').strip()
    db = load_database()
    user = find_user(db, username)

    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để xem tiến trình tu luyện!"})

    tl = user.get('tu_luyen', get_default_tu_luyen())
    n = tl["cap_hien_tai"]
    tieu_canh_idx = tl["tieu_canh"]
    diem_can_thiet = 100 * n + (tieu_canh_idx + 1) * 50 + (n ** 2) * 10

    return jsonify({
        "success": True,
        "data": tl,
        "ten_canh": TEN_CANH_GIOI[n - 1],
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
        "username": username,  # Lưu vết vĩnh viễn username của người đăng
        "icon": "📜" if cat == "truyen-dich" else "📖",
        "title": title,
        "content": content
    }
    db['books'].append(new_book)

    # Tăng điểm tu luyện
    user = find_user(db, username)
    if user:
        user.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50

    save_database(db, f"Tai len truyen: {title} (@{username})")
    return jsonify({"success": True, "message": f"Truyện [{title}] đã được lưu vào Động Phủ và Tàng Kinh Các vĩnh viễn!", "book": new_book})


@app.route('/api/delete-book', methods=['POST'])
def delete_book():
    data = request.json or {}
    book_id = data.get('id')
    username = data.get('username')

    db = load_database()
    user = find_user(db, username)

    if not user or user.get('role') != 'admin':
        return jsonify({"success": False, "message": "Chỉ có Chưởng Môn Nhân mới có quyền xóa tài nguyên tông môn!"})

    db['books'] = [b for b in db['books'] if b['id'] != book_id]
    save_database(db, f"Xoa truyen id={book_id} boi @{username}")
    return jsonify({"success": True, "message": "Đã xóa tài nguyên thành công!"})


# ============================================================
# API TÚI TRỮ VẬT (KHO RIÊNG THEO TÀI KHOẢN)
# ------------------------------------------------------------
# - Thính giả đọc truyện ở Tàng Kinh Các -> bấm "Lưu Túi Trữ Vật"
#   để cất một bản vào túi riêng của mình.
# - Trong Động Phủ: đọc lại truyện trong túi, thêm tài nguyên
#   riêng vào túi, hoặc "Đăng Tàng Kinh Các" để đưa tài nguyên
#   từ túi lên các mục truyện công khai của Tàng Kinh Các.
# - Túi gắn chặt với username: đăng nhập đúng tài khoản đã tạo
#   là thấy lại toàn bộ tài nguyên, bất kể code thay đổi ra sao.
# ============================================================
def _bag_item_meta(item):
    """Thông tin tóm tắt của 1 món trong túi (không kèm nội dung dài)."""
    return {
        "item_id": item["item_id"],
        "title": item["title"],
        "author": item.get("author", "Vô Danh"),
        "category": item.get("category", "truyen-dich"),
        "element": item.get("element", "Kim"),
        "icon": item.get("icon", "📖"),
        "chars": len(item.get("content", "")),
        "created_at": item.get("created_at", ""),
        "source_book_id": item.get("source_book_id"),
        "published_book_id": item.get("published_book_id")
    }


def _find_bag_item(user, item_id):
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    return next((i for i in user.get("bag", []) if i["item_id"] == item_id), None)


def _new_bag_item(user, title, content, author, cat, elem, source_book_id=None):
    new_id = max([i.get('item_id', 0) for i in user.get("bag", [])], default=0) + 1
    return {
        "item_id": new_id,
        "title": title,
        "content": content,
        "author": author,
        "category": cat,
        "element": elem,
        "icon": "📜" if cat == "truyen-dich" else "📖",
        "source_book_id": source_book_id,
        "published_book_id": None,
        "created_at": now_jst()
    }


@app.route('/api/bag', methods=['GET'])
def api_bag_list():
    username = request.args.get('username', '').strip()
    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để mở Túi Trữ Vật!"})
    items = [_bag_item_meta(i) for i in user.get("bag", [])]
    return jsonify({"success": True, "items": items})


@app.route('/api/bag/content', methods=['GET'])
def api_bag_content():
    username = request.args.get('username', '').strip()
    item_id = request.args.get('item_id')
    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để mở Túi Trữ Vật!"})
    item = _find_bag_item(user, item_id)
    if not item:
        return jsonify({"success": False, "message": "Không tìm thấy tài nguyên trong túi!"})
    return jsonify({"success": True, "title": item["title"], "author": item.get("author", ""),
                    "content": item.get("content", "")})


@app.route('/api/bag/add', methods=['POST'])
def api_bag_add():
    """Lưu một truyện từ Tàng Kinh Các vào Túi Trữ Vật của người đang đăng nhập."""
    data = request.json or {}
    username = data.get('username', '').strip()
    book_id = data.get('book_id')

    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để dùng Túi Trữ Vật!"})

    book = next((b for b in db['books'] if b.get('id') == book_id), None)
    if not book:
        return jsonify({"success": False, "message": "Không tìm thấy truyện này trong Tàng Kinh Các!"})

    if any(i.get("source_book_id") == book_id for i in user.get("bag", [])):
        return jsonify({"success": False, "message": f"Truyện [{book['title']}] đã có sẵn trong Túi Trữ Vật của đạo hữu!"})

    item = _new_bag_item(user, book["title"], book.get("content", ""), book.get("author", "Vô Danh"),
                         book.get("category", "truyen-dich"), book.get("element", "Kim"),
                         source_book_id=book["id"])
    user.setdefault("bag", []).append(item)
    save_database(db, f"@{username} luu truyen vao tui: {book['title']}")
    return jsonify({"success": True, "message": f"Đã cất truyện [{book['title']}] vào Túi Trữ Vật vĩnh viễn!", "item": _bag_item_meta(item)})


@app.route('/api/bag/upload', methods=['POST'])
def api_bag_upload():
    """Thêm một tài nguyên MỚI thẳng vào Túi Trữ Vật (chưa đăng công khai)."""
    data = request.json or {}
    username = data.get('username', '').strip()
    title = (data.get('title') or '').strip()
    content = data.get('content')
    author = data.get('author') or None
    cat = data.get('category', 'truyen-dich')
    elem = data.get('element', 'Kim')

    if not title or not content:
        return jsonify({"success": False, "message": "Thiếu tên truyện hoặc nội dung!"})

    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để dùng Túi Trữ Vật!"})

    item = _new_bag_item(user, title, content, author or user.get("author_name", "Vô Danh"), cat, elem)
    user.setdefault("bag", []).append(item)
    save_database(db, f"@{username} them tai nguyen vao tui: {title}")
    return jsonify({"success": True, "message": f"Đã cất [{title}] vào Túi Trữ Vật. Tài nguyên đang ở chế độ riêng tư, chưa đăng công khai.", "item": _bag_item_meta(item)})


@app.route('/api/bag/publish', methods=['POST'])
def api_bag_publish():
    """Đăng một tài nguyên từ Túi Trữ Vật lên Tàng Kinh Các (vào đúng mục truyện đã chọn)."""
    data = request.json or {}
    username = data.get('username', '').strip()
    item_id = data.get('item_id')
    cat = data.get('category')
    elem = data.get('element')

    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để dùng Túi Trữ Vật!"})

    item = _find_bag_item(user, item_id)
    if not item:
        return jsonify({"success": False, "message": "Không tìm thấy tài nguyên trong túi!"})

    cat = cat or item.get("category", "truyen-dich")
    elem = elem or item.get("element", "Kim")
    item["category"], item["element"] = cat, elem

    published_id = item.get("published_book_id")
    if published_id and any(b.get('id') == published_id for b in db['books']):
        return jsonify({"success": False, "message": f"Truyện [{item['title']}] đã được đăng ở Tàng Kinh Các rồi!"})

    new_id = max([b.get('id', 0) for b in db['books']], default=0) + 1
    new_book = {
        "id": new_id,
        "category": cat,
        "element": elem,
        "author": item.get("author", user.get("author_name", "Vô Danh")),
        "username": username,
        "icon": "📜" if cat == "truyen-dich" else "📖",
        "title": item["title"],
        "content": item.get("content", "")
    }
    db['books'].append(new_book)
    item["published_book_id"] = new_id
    item["source_book_id"] = item.get("source_book_id") or new_id

    user.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50
    save_database(db, f"@{username} dang truyen tu tui len Tang Kinh Cac: {item['title']}")
    return jsonify({"success": True,
                    "message": f"Đã đăng truyện [{item['title']}] lên Tàng Kinh Các (mục {cat} - hệ {elem}) thành công! +50 điểm tích lũy.",
                    "book": new_book})


@app.route('/api/bag/remove', methods=['POST'])
def api_bag_remove():
    """Xóa một món khỏi Túi Trữ Vật (bản đã đăng ở Tàng Kinh Các vẫn giữ nguyên)."""
    data = request.json or {}
    username = data.get('username', '').strip()
    item_id = data.get('item_id')

    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để dùng Túi Trữ Vật!"})

    item = _find_bag_item(user, item_id)
    if not item:
        return jsonify({"success": False, "message": "Không tìm thấy tài nguyên trong túi!"})

    user["bag"] = [i for i in user["bag"] if i["item_id"] != item["item_id"]]
    save_database(db, f"@{username} xoa khoi tui: {item['title']}")
    return jsonify({"success": True, "message": f"Đã lấy [{item['title']}] ra khỏi Túi Trữ Vật. (Bản đã đăng ở Tàng Kinh Các không bị ảnh hưởng.)"})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
