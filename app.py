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
        # Đợt 1 (web truyện + game): bổ sung dữ liệu game cho mọi tài khoản,
        # kể cả tài khoản cũ — không mất bất kỳ dữ liệu nào đã có.
        user["game"] = _ensure_game(user)
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
        _enforce_root_admin(data)
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


# ============================================================
# TÀI KHOẢN GỐC (ROOT ADMIN)
# ------------------------------------------------------------
# Tên đăng nhập mặc định "admin"; mật khẩu lấy từ biến môi
# trường ROOT_ADMIN_PASSWORD cài trên Render. Mỗi lần web khởi
# động, app tự đối chiếu: mật khẩu trong dữ liệu khác là cập
# nhật lại + lưu bền lên GitHub. Nhờ vậy tài khoản gốc luôn
# đăng nhập được bằng đúng mật khẩu ông chủ đặt, dùng để kiểm
# soát mọi tài khoản khác trên web.
# ============================================================
ROOT_ADMIN_USERNAME = os.environ.get("ROOT_ADMIN_USERNAME", "admin")
ROOT_ADMIN_PASSWORD = os.environ.get("ROOT_ADMIN_PASSWORD", "")


def _enforce_root_admin(data):
    if not ROOT_ADMIN_PASSWORD:
        return
    user = find_user(data, ROOT_ADMIN_USERNAME)
    if user is None:
        data["users"].append({
            "username": ROOT_ADMIN_USERNAME,
            "password": ROOT_ADMIN_PASSWORD,
            "role": "admin",
            "author_name": "Chưởng Môn Nhân",
            "tu_luyen": get_default_tu_luyen(),
            "bag": [],
        })
        save_database(data, "Tao tai khoan admin goc")
        return
    changed = False
    if user.get("password") != ROOT_ADMIN_PASSWORD:
        user["password"] = ROOT_ADMIN_PASSWORD
        changed = True
    if user.get("role") != "admin":
        user["role"] = "admin"
        changed = True
    if changed:
        save_database(data, "Cap nhat tai khoan admin goc")


def find_user(db, username):
    username = (username or "").strip()
    return next((u for u in db["users"] if u["username"] == username), None)


def public_user(user):
    """Bản sao thông tin user an toàn để trả về trình duyệt (không kèm mật khẩu/túi đồ)."""
    return {
        "username": user["username"],
        "role": user.get("role", "member"),
        "author_name": user.get("author_name", user["username"]),
        "tu_luyen": user.get("tu_luyen", get_default_tu_luyen()),
        "game": game_summary(user)
    }


def now_jst():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%d/%m/%Y %H:%M")


def _today_str():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


# ============================================================
# ĐỢT 1 — LỚP GAME TU LUYỆN (web vừa là truyện, vừa là game)
# ------------------------------------------------------------
# Mỗi tài khoản có thêm hồ sơ "game":
#   - Điểm Cống Hiến (diem): dòng tiền chính của toàn web.
#   - Kinh Nghiệm (exp): đo độ siêng, không tiêu được -> Cấp Hoạt Động.
#   - Tu Vi: 12 mức x 4 cấp nhỏ, lên cấp bằng Linh Khí.
#   - Linh Khí (linh_khi): đổi từ Linh Thạch 4 cấp.
#   - Linh Thạch: nguyen (10 LK) / vien (100) / vi (1.000) / vinh (10.000).
#   - Túi Trữ Vật giới hạn theo "capacity" (mặc định 10 truyện).
#   - Nhiệm vụ (tân thủ / hằng ngày / tông môn), thông báo, nhật ký.
# ============================================================
REALM_NAMES = ["Phàm Nhân", "Khai Linh", "Tụ Khí", "Trúc Cơ", "Kim Đan", "Nguyên Anh",
               "Hóa Thần", "Hợp Thể", "Đại Thừa", "Độ Kiếp", "Chân Tiên", "Đại Chúa Tể"]
STAGE_NAMES = ["Nhập Môn", "Tiểu Thành", "Đại Thành", "Viên Mãn"]
_STAGE_MULT = [1, 2, 4, 12]  # Nhập Môn x1, Tiểu Thành x2, Đại Thành x4, Viên Mãn x12 so với gốc mức

STONE_TYPES = {
    "nguyen": {"name": "Nguyên Tinh", "lk": 10, "price": 10},
    "vien":   {"name": "Viên Tinh",   "lk": 100, "price": 100},
    "vi":     {"name": "Vi Tinh",     "lk": 1000, "price": 1000},
    "vinh":   {"name": "Vĩnh Tinh",   "lk": 10000, "price": 10000},
}

TREASURES = [
    {"id": 1, "name": "Thanh Đồng Lệnh",     "price": 100,   "bonus_diem": 5,  "bonus_luyen": 0},
    {"id": 2, "name": "Bạch Ngân Ấn",        "price": 250,   "bonus_diem": 10, "bonus_luyen": 0},
    {"id": 3, "name": "Hoàng Kim Tỷ",        "price": 600,   "bonus_diem": 10, "bonus_luyen": 5},
    {"id": 4, "name": "Tử Ngọc Bội",         "price": 1400,  "bonus_diem": 15, "bonus_luyen": 5},
    {"id": 5, "name": "Huyền Tinh Châu",     "price": 3200,  "bonus_diem": 20, "bonus_luyen": 10},
    {"id": 6, "name": "Linh Tê Giác",        "price": 7500,  "bonus_diem": 25, "bonus_luyen": 10},
    {"id": 7, "name": "Tiên Ngọc Tỷ",        "price": 18000, "bonus_diem": 30, "bonus_luyen": 15},
    {"id": 8, "name": "Hỗn Nguyên Thạch",    "price": 45000, "bonus_diem": 40, "bonus_luyen": 20},
    {"id": 9, "name": "Tiên Thiên Bảo Thạch", "price": 120000, "bonus_diem": 50, "bonus_luyen": 25},
]

HERBS = [
    {"grade": 1, "name": "Tiểu Linh Thảo",    "price": 10},
    {"grade": 2, "name": "Thanh Linh Thảo",   "price": 25},
    {"grade": 3, "name": "Tử Linh Chi",       "price": 60},
    {"grade": 4, "name": "Huyết Sâm",         "price": 140},
    {"grade": 5, "name": "Băng Tâm Liên",     "price": 320},
    {"grade": 6, "name": "Hỏa Long Quả",      "price": 750},
    {"grade": 7, "name": "Ngọc Cốt Sâm",      "price": 1800},
    {"grade": 8, "name": "Cửu Diệp Linh Chi", "price": 4500},
    {"grade": 9, "name": "Thái Thượng Tiên Thảo", "price": 12000},
]

BAG_DEFAULT_CAPACITY = 10
CAPACITY_PACKS = [
    {"add": 5,  "price": 150,  "name": "+5 ô truyện"},
    {"add": 10, "price": 300,  "name": "+10 ô truyện"},
    {"add": 25, "price": 700,  "name": "+25 ô truyện"},
    {"add": -1, "price": 2000, "name": "Túi Càn Khôn (không giới hạn)"},
]

# Nhiệm vụ: counter lấy từ stats của người chơi (doc_chuong / cat_tui /
# dang_truyen / doi_linh_thach / dot_pha). Tân thủ tính theo tổng lũy kế,
# hằng ngày & tông môn tính theo ngày (từ lúc nhận nhiệm vụ).
MISSIONS = [
    # --- Chuỗi tân thủ (làm lần lượt) ---
    {"id": "nb1", "group": "tan_thu", "name": "Bước Vào Tông Môn", "desc": "Đọc / nghe 1 chương truyện bất kỳ", "counter": "doc_chuong", "target": 1, "reward": {"diem": 20, "exp": 10}},
    {"id": "nb2", "group": "tan_thu", "name": "Cất Giữ Đầu Tiên", "desc": "Cất 1 truyện vào Túi Trữ Vật", "counter": "cat_tui", "target": 1, "reward": {"diem": 20, "exp": 10}},
    {"id": "nb3", "group": "tan_thu", "name": "Cống Hiến Đầu Tiên", "desc": "Đăng 1 truyện lên Tàng Kinh Các", "counter": "dang_truyen", "target": 1, "reward": {"diem": 30, "exp": 20}},
    {"id": "nb4", "group": "tan_thu", "name": "Linh Thạch Đầu Tiên", "desc": "Đổi Linh Thạch ra Linh Khí 1 lần tại Sảnh Chính", "counter": "doi_linh_thach", "target": 1, "reward": {"exp": 10, "stones": {"nguyen": 1}}},
    {"id": "nb5", "group": "tan_thu", "name": "Nhập Môn Tu Luyện", "desc": "Nâng Tu Vi lên Phàm Nhân · Nhập Môn (cần 50 Linh Khí = 5 Nguyên Tinh)", "counter": "dot_pha", "target": 1, "reward": {"diem": 100, "exp": 30, "stones": {"nguyen": 1}}},
    # --- Hằng ngày ---
    {"id": "d_doc3", "group": "hang_ngay", "name": "Học Sĩ Chăm Chỉ", "desc": "Đọc / nghe 3 chương truyện hôm nay", "counter": "doc_chuong", "target": 3, "reward": {"diem": 30, "exp": 30}},
    {"id": "d_cat1", "group": "hang_ngay", "name": "Thu Gom Tài Nguyên", "desc": "Cất 1 truyện vào Túi Trữ Vật hôm nay", "counter": "cat_tui", "target": 1, "reward": {"diem": 15, "exp": 10}},
    {"id": "d_dang1", "group": "hang_ngay", "name": "Cống Hiến Hằng Ngày", "desc": "Đăng 1 truyện lên Tàng Kinh Các hôm nay", "counter": "dang_truyen", "target": 1, "reward": {"diem": 50, "exp": 20}},
    {"id": "d_doi1", "group": "hang_ngay", "name": "Tụ Linh Hằng Ngày", "desc": "Đổi Linh Thạch ra Linh Khí 1 lần hôm nay", "counter": "doi_linh_thach", "target": 1, "reward": {"diem": 10, "exp": 5}},
    # --- Tông môn (thưởng Linh Thạch theo hạng) ---
    {"id": "f_doc5", "group": "tong_mon", "rank": "F", "unlock": 1, "name": "[F] Trừ Tà Ngoại Vi", "desc": "Đọc / nghe 5 chương truyện hôm nay", "counter": "doc_chuong", "target": 5, "reward": {"exp": 20, "stones": {"nguyen": 3}}},
    {"id": "f_dang2", "group": "tong_mon", "rank": "F", "unlock": 1, "name": "[F] Thu Thập Kinh Văn", "desc": "Đăng 2 truyện lên Tàng Kinh Các hôm nay", "counter": "dang_truyen", "target": 2, "reward": {"exp": 20, "stones": {"nguyen": 3}}},
    {"id": "f_cat3", "group": "tong_mon", "rank": "F", "unlock": 1, "name": "[F] Tích Trữ Lương Thảo", "desc": "Cất 3 truyện vào Túi Trữ Vật hôm nay", "counter": "cat_tui", "target": 3, "reward": {"exp": 20, "stones": {"nguyen": 3}}},
    {"id": "e_doc15", "group": "tong_mon", "rank": "E", "unlock": 1, "name": "[E] Thông Kinh Bác Cổ", "desc": "Đọc / nghe 15 chương truyện hôm nay", "counter": "doc_chuong", "target": 15, "reward": {"exp": 50, "stones": {"nguyen": 30}}},
    {"id": "e_dang5", "group": "tong_mon", "rank": "E", "unlock": 1, "name": "[E] Quảng Truyền Kinh Điển", "desc": "Đăng 5 truyện lên Tàng Kinh Các hôm nay", "counter": "dang_truyen", "target": 5, "reward": {"exp": 50, "stones": {"nguyen": 30}}},
    {"id": "d_doc40", "group": "tong_mon", "rank": "D", "unlock": 3, "name": "[D] Đại Hội Giảng Kinh", "desc": "Đọc / nghe 40 chương truyện hôm nay (mở khóa từ mức Tụ Khí)", "counter": "doc_chuong", "target": 40, "reward": {"exp": 150, "stones": {"nguyen": 300}}},
]
NEWBIE_ORDER = ["nb1", "nb2", "nb3", "nb4", "nb5"]


def stage_cost(muc, cap):
    """Linh Khí cần để ĐẠT cấp (muc, cap): Nhập Môn mức 1 = 50, mỗi mức sau x12 điểm cuối."""
    return 50 * (12 ** (muc - 1)) * _STAGE_MULT[cap]


def default_game(user):
    diem0 = 120
    try:
        diem0 = int(user.get("tu_luyen", {}).get("diem_tich_tuyem", 120))
    except Exception:
        diem0 = 120
    return {
        "diem": diem0,
        "exp": 0,
        "tu_vi": {"muc": 1, "cap": -1, "break_missions": 0},
        "linh_khi": 0,
        "linh_thach": {"nguyen": 0, "vien": 0, "vi": 0, "vinh": 0},
        "capacity": BAG_DEFAULT_CAPACITY,
        "inventory": {"treasures": [], "herbs": {}, "pha_canh_dan": 0},
        "stats": {"total": {}, "today": {"date": _today_str()}},
        "missions": {"active": {}, "claimed_today": [], "newbie_done": []},
        "notifications": [],
        "history": [],
        "streak": {"last": "", "count": 0},
        "locked": False,
    }


def _rollover(user):
    """Qua ngày mới (giờ Nhật): reset bộ đếm ngày + hết hạn nhiệm vụ ngày/tông môn đang nhận."""
    g = user.get("game") or {}
    today = (g.get("stats") or {}).get("today") or {}
    if today.get("date") != _today_str():
        g.setdefault("stats", {})["today"] = {"date": _today_str()}
        g.setdefault("missions", {})["active"] = {}
        g["missions"]["claimed_today"] = []


def _ensure_game(user):
    g = user.get("game")
    if not isinstance(g, dict):
        g = default_game(user)
        user["game"] = g
        return g
    d = default_game(user)
    for k, v in d.items():
        if k not in g:
            g[k] = v
        elif isinstance(v, dict) and isinstance(g[k], dict):
            for kk, vv in v.items():
                if kk not in g[k]:
                    g[k][kk] = vv
    _rollover(user)
    return g


def _log(user, text):
    g = _ensure_game(user)
    g["history"].append({"time": now_jst(), "text": text})
    g["history"] = g["history"][-100:]


def _notify(user, text):
    g = _ensure_game(user)
    g["notifications"].append({"time": now_jst(), "text": text, "read": False})
    g["notifications"] = g["notifications"][-50:]


def _add_diem(user, n, reason=None):
    g = _ensure_game(user)
    g["diem"] = max(0, int(g.get("diem", 0)) + int(n))
    if reason and n:
        _log(user, f"{reason} ({'+' if n > 0 else ''}{n} Điểm Cống Hiến)")


def _add_exp(user, n):
    g = _ensure_game(user)
    g["exp"] = max(0, int(g.get("exp", 0)) + int(n))


def _add_stone(user, loai, n):
    g = _ensure_game(user)
    g["linh_thach"][loai] = max(0, int(g["linh_thach"].get(loai, 0)) + int(n))


def _bump(user, key, n=1):
    g = _ensure_game(user)
    total = g["stats"].setdefault("total", {})
    total[key] = int(total.get(key, 0)) + n
    today = g["stats"].setdefault("today", {"date": _today_str()})
    today[key] = int(today.get(key, 0)) + n


def _treasure_bonus_diem(user):
    g = _ensure_game(user)
    owned = set(g["inventory"].get("treasures", []))
    return sum(t["bonus_diem"] for t in TREASURES if t["id"] in owned)


def _bag_capacity(user):
    """Dung lượng túi hiệu dụng: None = không giới hạn (admin hoặc Túi Càn Khôn)."""
    if user.get("role") == "admin":
        return None
    cap = _ensure_game(user).get("capacity", BAG_DEFAULT_CAPACITY)
    if cap is None or int(cap) < 0:
        return None
    return int(cap)


def _bag_full_error(user):
    cap = _bag_capacity(user)
    if cap is None:
        return None
    count = len(user.get("bag", []))
    if count >= cap:
        return (f"Túi Trữ Vật đã đầy ({count}/{cap} truyện)! Hãy tới Sảnh Chính đổi thêm "
                f"dung lượng bằng Điểm Cống Hiến, hoặc xóa bớt truyện trong túi.")
    return None


def game_summary(user):
    g = _ensure_game(user)
    tv = g["tu_vi"]
    muc, cap = int(tv.get("muc", 1)), int(tv.get("cap", -1))
    if muc >= 12 and cap >= 3:
        next_cost = None
    elif cap >= 3:
        next_cost = stage_cost(muc + 1, 0)
    else:
        next_cost = stage_cost(muc, cap + 1)
    cap_hd = 1 + int(g.get("exp", 0)) // 250
    return {
        "diem": g.get("diem", 0),
        "exp": g.get("exp", 0),
        "cap_hoat_dong": cap_hd,
        "tu_vi": {
            "muc": muc, "ten_muc": REALM_NAMES[muc - 1],
            "cap": cap, "ten_cap": STAGE_NAMES[cap] if cap >= 0 else "Chưa Nhập Môn",
            "linh_khi": g.get("linh_khi", 0), "next_cost": next_cost,
            "break_missions": tv.get("break_missions", 0),
        },
        "linh_thach": g.get("linh_thach", {}),
        "capacity": _bag_capacity(user),
        "bag_count": len(user.get("bag", [])),
        "inventory": g.get("inventory", {}),
        "streak": g.get("streak", {}),
        "checked_in_today": bool(g["stats"].get("today", {}).get("diem_danh")),
        "unread": sum(1 for n in g.get("notifications", []) if not n.get("read")),
    }


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
    # Lớp game: đọc / nghe xong 1 chương được +10 Kinh Nghiệm và tính vào
    # tiến độ nhiệm vụ. Ghi bền mỗi 5 chương để tránh ghi dữ liệu quá dày.
    try:
        uname = session.get('username')
        if uname:
            db = load_database()
            u = find_user(db, uname)
            if u:
                _bump(u, "doc_chuong")
                _add_exp(u, 10)
                if _ensure_game(u)["stats"]["total"].get("doc_chuong", 0) % 5 == 0:
                    save_database(db, f"@{uname} doc truyen")
    except Exception:
        pass
    return render_template('doc_truyen.html', chapter_title=title, chapter_content=content)


# --- API XÁC THỰC & ĐĂNG NHẬP ---
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    db = load_database()
    user = next((u for u in db['users'] if u['username'] == username and u['password'] == password), None)

    if user and _ensure_game(user).get("locked"):
        return jsonify({"success": False, "message": "Tài khoản này đã bị Tông Môn khóa! Hãy liên hệ Chưởng Môn Nhân."})

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
    new_user["game"] = default_game(new_user)
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
        _add_diem(user, 50, f"Đăng truyện [{title}] lên Tàng Kinh Các")
        _add_exp(user, 50)
        _bump(user, "dang_truyen")

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
    cap = _bag_capacity(user)
    return jsonify({"success": True, "items": items, "count": len(items),
                    "capacity": cap, "unlimited": cap is None})


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

    full = _bag_full_error(user)
    if full:
        return jsonify({"success": False, "message": full})

    item = _new_bag_item(user, book["title"], book.get("content", ""), book.get("author", "Vô Danh"),
                         book.get("category", "truyen-dich"), book.get("element", "Kim"),
                         source_book_id=book["id"])
    user.setdefault("bag", []).append(item)
    _bump(user, "cat_tui")
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

    full = _bag_full_error(user)
    if full:
        return jsonify({"success": False, "message": full})

    item = _new_bag_item(user, title, content, author or user.get("author_name", "Vô Danh"), cat, elem)
    user.setdefault("bag", []).append(item)
    _bump(user, "cat_tui")
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
    _add_diem(user, 50, f"Đăng truyện [{item['title']}] từ Túi lên Tàng Kinh Các")
    _add_exp(user, 50)
    _bump(user, "dang_truyen")
    save_database(db, f"@{username} dang truyen tu tui len Tang Kinh Cac: {item['title']}")
    return jsonify({"success": True,
                    "message": f"Đã đăng truyện [{item['title']}] lên Tàng Kinh Các (mục {cat} - hệ {elem}) thành công! +50 Điểm Cống Hiến.",
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


# ============================================================
# ĐỢT 1 — API GAME: SẢNH CHÍNH, NHIỆM VỤ, TU VI, QUẢN TRỊ
# ============================================================
@app.route('/sanh-chinh')
def sanh_chinh():
    return render_template('sanh_chinh.html')


@app.route('/quan-tri')
def quan_tri():
    db = load_database()
    u = find_user(db, session.get('username', ''))
    if not u or u.get('role') != 'admin':
        return redirect(url_for('home'))
    return render_template('quan_tri.html')


def _session_user(db):
    uname = session.get('username')
    if not uname:
        return None
    return find_user(db, uname)


def _game_or_401(db):
    u = _session_user(db)
    if not u:
        return None, (jsonify({"success": False, "message": "Cần đăng nhập!"}), 401)
    return u, None


# --- Điểm danh & chuỗi ngày ---
@app.route('/api/checkin', methods=['POST'])
def api_checkin():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    today = _today_str()
    if g["stats"].get("today", {}).get("diem_danh"):
        return jsonify({"success": False, "message": "Đạo hữu đã điểm danh hôm nay rồi!"})
    _bump(user, "diem_danh")
    yesterday = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=1)).strftime("%Y-%m-%d")
    if g["streak"].get("last") == yesterday:
        g["streak"]["count"] = int(g["streak"].get("count", 0)) + 1
    elif g["streak"].get("last") != today:
        g["streak"]["count"] = 1
    g["streak"]["last"] = today
    msgs = [f"Điểm danh thành công! Chuỗi ngày hiện tại: {g['streak']['count']} ngày. Hãy nhận thưởng ở nhiệm vụ hằng ngày."]
    if g["streak"]["count"] == 7:
        _add_stone(user, "nguyen", 1)
        _notify(user, "🔥 Điểm danh đủ 7 ngày liên tiếp: Tông Môn thưởng 1 Nguyên Tinh!")
        msgs.append("Đủ 7 ngày liên tiếp: thưởng 1 Nguyên Tinh!")
    if g["streak"]["count"] == 30:
        _add_stone(user, "vien", 1)
        _notify(user, "🔥 Điểm danh đủ 30 ngày liên tiếp: Tông Môn thưởng 1 Viên Tinh!")
        msgs.append("Đủ 30 ngày liên tiếp: thưởng 1 Viên Tinh!")
    save_database(db, f"@{user['username']} diem danh")
    return jsonify({"success": True, "message": " ".join(msgs), "game": game_summary(user)})


# --- Tu Vi: nâng cấp / đột phá ---
@app.route('/api/tu-vi/nang-cap', methods=['POST'])
def api_tu_vi_nang_cap():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    tv = g["tu_vi"]
    muc, cap = int(tv.get("muc", 1)), int(tv.get("cap", -1))

    if muc >= 12 and cap >= 3:
        return jsonify({"success": False, "message": "Đạo hữu đã đạt Đại Chúa Tể · Viên Mãn — đỉnh cao tuyệt đối!"})

    if cap >= 3:
        # Đột phá sang mức mới: cần Phá Cảnh Đan (Tông Môn cấp sau 3 nhiệm vụ ở Viên Mãn)
        if int(g["inventory"].get("pha_canh_dan", 0)) < 1:
            need = max(0, 3 - int(tv.get("break_missions", 0)))
            if need > 0:
                return jsonify({"success": False, "message": f"Muốn đột phá phải hoàn thành thêm {need} Nhiệm Vụ Tông Môn nữa (khi đang ở Viên Mãn), Tông Môn sẽ cấp Phá Cảnh Đan."})
            return jsonify({"success": False, "message": "Đạo hữu chưa có Phá Cảnh Đan!"})
        cost = stage_cost(muc + 1, 0)
        if int(g.get("linh_khi", 0)) < cost:
            return jsonify({"success": False, "message": f"Chưa đủ Linh Khí để đột phá: cần {cost}, đang có {g.get('linh_khi', 0)}."})
        g["linh_khi"] -= cost
        g["inventory"]["pha_canh_dan"] -= 1
        tv["muc"], tv["cap"], tv["break_missions"] = muc + 1, 0, 0
        _bump(user, "dot_pha")
        _log(user, f"Đột phá thành công lên {REALM_NAMES[muc]} · Nhập Môn (-{cost} Linh Khí, dùng 1 Phá Cảnh Đan)")
        _notify(user, f"🌟 Chúc mừng! Đạo hữu đã đột phá lên {REALM_NAMES[muc]} · Nhập Môn!")
        save_database(db, f"@{user['username']} dot pha len {REALM_NAMES[muc]}")
        return jsonify({"success": True, "message": f"Đột phá thành công! Chào mừng đạo hữu bước vào {REALM_NAMES[muc]} · Nhập Môn.", "game": game_summary(user)})

    target_cap = cap + 1
    cost = stage_cost(muc, target_cap)
    if int(g.get("linh_khi", 0)) < cost:
        return jsonify({"success": False, "message": f"Chưa đủ Linh Khí: cần {cost} để lên {STAGE_NAMES[target_cap]}, đang có {g.get('linh_khi', 0)}. Hãy đổi Linh Thạch tại Sảnh Chính."})
    g["linh_khi"] -= cost
    tv["cap"] = target_cap
    _bump(user, "dot_pha")
    _log(user, f"Nâng Tu Vi lên {REALM_NAMES[muc - 1]} · {STAGE_NAMES[target_cap]} (-{cost} Linh Khí)")
    save_database(db, f"@{user['username']} nang tu vi {REALM_NAMES[muc - 1]} {STAGE_NAMES[target_cap]}")
    return jsonify({"success": True, "message": f"Nâng cấp thành công: {REALM_NAMES[muc - 1]} · {STAGE_NAMES[target_cap]}!", "game": game_summary(user)})


# --- Sảnh Chính: các quầy đổi điểm ---
@app.route('/api/shop/linh-thach', methods=['POST'])
def api_shop_linh_thach():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    loai = data.get('type')
    qty = max(1, min(1000, int(data.get('qty', 1) or 1)))
    if loai not in STONE_TYPES:
        return jsonify({"success": False, "message": "Loại linh thạch không hợp lệ!"})
    g = _ensure_game(user)
    cost = STONE_TYPES[loai]["price"] * qty
    if int(g.get("diem", 0)) < cost:
        return jsonify({"success": False, "message": f"Không đủ Điểm Cống Hiến: cần {cost}, đang có {g.get('diem', 0)}."})
    _add_diem(user, -cost, f"Mua {qty} {STONE_TYPES[loai]['name']} tại Sảnh Chính")
    _add_stone(user, loai, qty)
    save_database(db, f"@{user['username']} mua linh thach {loai} x{qty}")
    return jsonify({"success": True, "message": f"Đã mua {qty} {STONE_TYPES[loai]['name']} (-{cost} điểm).", "game": game_summary(user)})


@app.route('/api/shop/doi-linh-khi', methods=['POST'])
def api_shop_doi_linh_khi():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    loai = data.get('type')
    if loai not in STONE_TYPES:
        return jsonify({"success": False, "message": "Loại linh thạch không hợp lệ!"})
    g = _ensure_game(user)
    have = int(g["linh_thach"].get(loai, 0))
    qty = have if data.get('qty') == 'all' else max(1, int(data.get('qty', 1) or 1))
    if have < qty or qty < 1:
        return jsonify({"success": False, "message": f"Không đủ {STONE_TYPES[loai]['name']} để đổi (đang có {have})."})
    lk = STONE_TYPES[loai]["lk"] * qty
    _add_stone(user, loai, -qty)
    g["linh_khi"] = int(g.get("linh_khi", 0)) + lk
    _bump(user, "doi_linh_thach")
    _log(user, f"Đổi {qty} {STONE_TYPES[loai]['name']} -> +{lk} Linh Khí")
    save_database(db, f"@{user['username']} doi {qty} {loai} thanh linh khi")
    return jsonify({"success": True, "message": f"Đã đổi {qty} {STONE_TYPES[loai]['name']} thành +{lk} Linh Khí!", "game": game_summary(user)})


@app.route('/api/shop/dung-luong', methods=['POST'])
def api_shop_dung_luong():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    idx = int(data.get('pack', -1))
    if idx < 0 or idx >= len(CAPACITY_PACKS):
        return jsonify({"success": False, "message": "Gói dung lượng không hợp lệ!"})
    g = _ensure_game(user)
    if _bag_capacity(user) is None:
        return jsonify({"success": False, "message": "Túi của đạo hữu đã không giới hạn rồi!"})
    pack = CAPACITY_PACKS[idx]
    if int(g.get("diem", 0)) < pack["price"]:
        return jsonify({"success": False, "message": f"Không đủ Điểm Cống Hiến: cần {pack['price']}, đang có {g.get('diem', 0)}."})
    _add_diem(user, -pack["price"], f"Mua gói dung lượng túi: {pack['name']}")
    if pack["add"] < 0:
        g["capacity"] = -1
    else:
        g["capacity"] = int(g.get("capacity", BAG_DEFAULT_CAPACITY)) + pack["add"]
    save_database(db, f"@{user['username']} mua dung luong tui: {pack['name']}")
    return jsonify({"success": True, "message": f"Đã mua {pack['name']} (-{pack['price']} điểm).", "game": game_summary(user)})


@app.route('/api/shop/bao-vat', methods=['POST'])
def api_shop_bao_vat():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    tid = int((request.json or {}).get('id', 0))
    item = next((t for t in TREASURES if t["id"] == tid), None)
    if not item:
        return jsonify({"success": False, "message": "Bảo vật không tồn tại!"})
    g = _ensure_game(user)
    if tid in g["inventory"].get("treasures", []):
        return jsonify({"success": False, "message": f"Đạo hữu đã sở hữu {item['name']} rồi!"})
    if int(g.get("diem", 0)) < item["price"]:
        return jsonify({"success": False, "message": f"Không đủ Điểm Cống Hiến: cần {item['price']}, đang có {g.get('diem', 0)}."})
    _add_diem(user, -item["price"], f"Mua bảo vật {item['name']}")
    g["inventory"].setdefault("treasures", []).append(tid)
    _notify(user, f"💎 Đã sở hữu bảo vật {item['name']} (+{item['bonus_diem']}% điểm nhiệm vụ).")
    save_database(db, f"@{user['username']} mua bao vat {item['name']}")
    return jsonify({"success": True, "message": f"Đã mua {item['name']} (-{item['price']} điểm).", "game": game_summary(user)})


@app.route('/api/shop/thao-moc', methods=['POST'])
def api_shop_thao_moc():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    grade = int(data.get('grade', 0))
    qty = max(1, min(999, int(data.get('qty', 1) or 1)))
    herb = next((h for h in HERBS if h["grade"] == grade), None)
    if not herb:
        return jsonify({"success": False, "message": "Thảo mộc không hợp lệ!"})
    g = _ensure_game(user)
    cost = herb["price"] * qty
    if int(g.get("diem", 0)) < cost:
        return jsonify({"success": False, "message": f"Không đủ Điểm Cống Hiến: cần {cost}, đang có {g.get('diem', 0)}."})
    _add_diem(user, -cost, f"Mua {qty} {herb['name']}")
    herbs = g["inventory"].setdefault("herbs", {})
    herbs[str(grade)] = int(herbs.get(str(grade), 0)) + qty
    save_database(db, f"@{user['username']} mua thao moc cap {grade} x{qty}")
    return jsonify({"success": True, "message": f"Đã mua {qty} {herb['name']} (-{cost} điểm).", "game": game_summary(user)})


@app.route('/api/shop/thu-hoi', methods=['POST'])
def api_shop_thu_hoi():
    """Tông Môn thu hồi thảo mộc dư, trả 50% giá bằng Điểm Cống Hiến."""
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    grade = int(data.get('grade', 0))
    qty = max(1, int(data.get('qty', 1) or 1))
    herb = next((h for h in HERBS if h["grade"] == grade), None)
    if not herb:
        return jsonify({"success": False, "message": "Thảo mộc không hợp lệ!"})
    g = _ensure_game(user)
    herbs = g["inventory"].setdefault("herbs", {})
    have = int(herbs.get(str(grade), 0))
    if have < qty:
        return jsonify({"success": False, "message": f"Không đủ {herb['name']} để bán (đang có {have})."})
    gain = (herb["price"] // 2) * qty
    herbs[str(grade)] = have - qty
    _add_diem(user, gain, f"Bán {qty} {herb['name']} cho Quầy Thu Hồi")
    save_database(db, f"@{user['username']} ban thao moc cap {grade} x{qty}")
    return jsonify({"success": True, "message": f"Đã bán {qty} {herb['name']}, nhận +{gain} Điểm Cống Hiến.", "game": game_summary(user)})


# --- Nhiệm vụ Tông Môn ---
def _mission_def(mid):
    return next((m for m in MISSIONS if m["id"] == mid), None)


def _mission_progress(user, m):
    g = _ensure_game(user)
    if m["group"] == "tan_thu":
        cur = int(g["stats"].get("total", {}).get(m["counter"], 0))
        return min(cur, m["target"])
    act = g["missions"].get("active", {}).get(m["id"])
    if not act:
        return 0
    cur = int(g["stats"].get("today", {}).get(m["counter"], 0))
    return min(max(0, cur - int(act.get("base", 0))), m["target"])


def _mission_state(user, m):
    g = _ensure_game(user)
    ms = g["missions"]
    if m["group"] == "tan_thu":
        if m["id"] in ms.get("newbie_done", []):
            return "done"
        idx = NEWBIE_ORDER.index(m["id"]) if m["id"] in NEWBIE_ORDER else 99
        return "current" if idx == len(ms.get("newbie_done", [])) else "locked"
    if m["group"] == "tong_mon" and int(g["tu_vi"].get("muc", 1)) < int(m.get("unlock", 1)):
        return "locked_realm"
    if m["id"] in ms.get("claimed_today", []):
        return "claimed"
    if m["id"] in ms.get("active", {}):
        return "active"
    return "available"


@app.route('/api/missions', methods=['GET'])
def api_missions():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    out = []
    for m in MISSIONS:
        state = _mission_state(user, m)
        prog = _mission_progress(user, m)
        out.append({
            "id": m["id"], "group": m["group"], "rank": m.get("rank"),
            "name": m["name"], "desc": m["desc"], "target": m["target"],
            "progress": prog, "state": state,
            "claimable": state in ("active", "current") and prog >= m["target"],
            "reward": m["reward"],
        })
    return jsonify({"success": True, "missions": out, "game": game_summary(user)})


@app.route('/api/missions/nhan', methods=['POST'])
def api_missions_nhan():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _mission_def((request.json or {}).get('id'))
    if not m:
        return jsonify({"success": False, "message": "Nhiệm vụ không tồn tại!"})
    if m["group"] == "tan_thu":
        return jsonify({"success": False, "message": "Nhiệm vụ tân thủ luôn sẵn sàng, không cần nhận!"})
    g = _ensure_game(user)
    state = _mission_state(user, m)
    if state == "locked_realm":
        return jsonify({"success": False, "message": "Tu Vi chưa đủ để nhận nhiệm vụ hạng này!"})
    if state == "claimed":
        return jsonify({"success": False, "message": "Nhiệm vụ này hôm nay đã hoàn thành rồi!"})
    if state == "active":
        return jsonify({"success": False, "message": "Đạo hữu đang làm nhiệm vụ này rồi!"})
    if len(g["missions"].get("active", {})) >= 5:
        return jsonify({"success": False, "message": "Đạo hữu đang nhận tối đa 5 nhiệm vụ cùng lúc! Hãy hoàn thành bớt rồi nhận tiếp."})
    base = int(g["stats"].get("today", {}).get(m["counter"], 0))
    g["missions"].setdefault("active", {})[m["id"]] = {"base": base}
    save_database(db, f"@{user['username']} nhan nhiem vu {m['id']}")
    return jsonify({"success": True, "message": f"Đã nhận nhiệm vụ [{m['name']}]. Tiến độ bắt đầu được tính từ bây giờ.", "game": game_summary(user)})


@app.route('/api/missions/nhan-thuong', methods=['POST'])
def api_missions_nhan_thuong():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _mission_def((request.json or {}).get('id'))
    if not m:
        return jsonify({"success": False, "message": "Nhiệm vụ không tồn tại!"})
    g = _ensure_game(user)
    state = _mission_state(user, m)
    if state not in ("active", "current"):
        return jsonify({"success": False, "message": "Nhiệm vụ chưa sẵn sàng để nhận thưởng!"})
    if _mission_progress(user, m) < m["target"]:
        return jsonify({"success": False, "message": "Chưa đủ tiến độ để nhận thưởng!"})
    rw = m["reward"]
    parts = []
    if rw.get("diem"):
        gain = int(round(rw["diem"] * (1 + _treasure_bonus_diem(user) / 100.0)))
        _add_diem(user, gain, f"Thưởng nhiệm vụ [{m['name']}]")
        parts.append(f"+{gain} Điểm Cống Hiến")
    if rw.get("exp"):
        _add_exp(user, rw["exp"])
        parts.append(f"+{rw['exp']} Kinh Nghiệm")
    for loai, n in (rw.get("stones") or {}).items():
        _add_stone(user, loai, n)
        parts.append(f"+{n} {STONE_TYPES[loai]['name']}")
    ms = g["missions"]
    if m["group"] == "tan_thu":
        ms.setdefault("newbie_done", []).append(m["id"])
    else:
        ms.setdefault("claimed_today", []).append(m["id"])
        ms.get("active", {}).pop(m["id"], None)
    # Điều kiện đột phá: hoàn thành nhiệm vụ khi đang ở Viên Mãn
    tv = g["tu_vi"]
    if int(tv.get("cap", -1)) == 3:
        tv["break_missions"] = int(tv.get("break_missions", 0)) + 1
        if tv["break_missions"] >= 3 and int(g["inventory"].get("pha_canh_dan", 0)) < 1:
            g["inventory"]["pha_canh_dan"] = 1
            _notify(user, "🌟 Đạo hữu đã hoàn thành đủ 3 nhiệm vụ ở Viên Mãn — Tông Môn cấp 1 viên Phá Cảnh Đan! Vào Sảnh Chính để đột phá.")
            parts.append("Nhận Phá Cảnh Đan từ Tông Môn!")
    save_database(db, f"@{user['username']} nhan thuong nhiem vu {m['id']}")
    return jsonify({"success": True, "message": f"Hoàn thành [{m['name']}]: " + ", ".join(parts) + ".", "game": game_summary(user)})


# --- Thông báo & nhật ký ---
@app.route('/api/notifications', methods=['GET'])
def api_notifications():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    return jsonify({"success": True, "notifications": list(reversed(g.get("notifications", [])))})


@app.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    for n in g.get("notifications", []):
        n["read"] = True
    save_database(db, f"@{user['username']} doc thong bao")
    return jsonify({"success": True})


@app.route('/api/history', methods=['GET'])
def api_history():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    return jsonify({"success": True, "history": list(reversed(g.get("history", [])))})


# --- Quản trị (chỉ tài khoản gốc / admin) ---
def _admin_or_403(db):
    u = _session_user(db)
    if not u or u.get('role') != 'admin':
        return None, (jsonify({"success": False, "message": "Chỉ Chưởng Môn Nhân (admin) mới dùng được mục này!"}), 403)
    return u, None


@app.route('/api/admin/users', methods=['GET'])
def api_admin_users():
    db = load_database()
    _, err = _admin_or_403(db)
    if err:
        return err
    out = []
    for u in db['users']:
        g = _ensure_game(u)
        tv = g["tu_vi"]
        out.append({
            "username": u["username"], "author_name": u.get("author_name", ""),
            "role": u.get("role", "member"), "locked": bool(g.get("locked")),
            "diem": g.get("diem", 0), "exp": g.get("exp", 0),
            "tu_vi": f"{REALM_NAMES[int(tv.get('muc', 1)) - 1]} · {(STAGE_NAMES[int(tv['cap'])] if int(tv.get('cap', -1)) >= 0 else 'Chưa Nhập Môn')}",
            "linh_khi": g.get("linh_khi", 0), "linh_thach": g.get("linh_thach", {}),
            "bag_count": len(u.get("bag", [])), "capacity": _bag_capacity(u),
        })
    return jsonify({"success": True, "users": out})


@app.route('/api/admin/create-user', methods=['POST'])
def api_admin_create_user():
    db = load_database()
    _, err = _admin_or_403(db)
    if err:
        return err
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    author_name = (data.get('author_name') or '').strip()
    if not username or not password or not author_name:
        return jsonify({"success": False, "message": "Thiếu thông tin tài khoản!"})
    if find_user(db, username):
        return jsonify({"success": False, "message": "Tài khoản đã tồn tại!"})
    new_user = {"username": username, "password": password, "role": "member",
                "author_name": author_name, "tu_luyen": get_default_tu_luyen(), "bag": []}
    new_user["game"] = default_game(new_user)
    db['users'].append(new_user)
    save_database(db, f"Admin tao tai khoan: {username}")
    return jsonify({"success": True, "message": f"Đã tạo tài khoản [{username}] ({author_name})."})


@app.route('/api/admin/user-action', methods=['POST'])
def api_admin_user_action():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    data = request.json or {}
    target = find_user(db, (data.get('username') or '').strip())
    action = data.get('action')
    value = data.get('value')
    if not target:
        return jsonify({"success": False, "message": "Không tìm thấy tài khoản!"})
    g = _ensure_game(target)
    if action == "lock":
        g["locked"] = True
        msg = f"Đã khóa tài khoản [{target['username']}]."
    elif action == "unlock":
        g["locked"] = False
        msg = f"Đã mở khóa tài khoản [{target['username']}]."
    elif action == "reset_password":
        if not value or len(str(value)) < 4:
            return jsonify({"success": False, "message": "Mật khẩu mới phải có ít nhất 4 ký tự!"})
        target["password"] = str(value)
        _notify(target, "🔑 Chưởng Môn Nhân đã đặt lại mật khẩu cho tài khoản của đạo hữu.")
        msg = f"Đã đặt lại mật khẩu cho [{target['username']}]."
    elif action == "add_diem":
        n = int(value or 0)
        _add_diem(target, n, f"Chưởng Môn Nhân điều chỉnh điểm cho @{target['username']}")
        _notify(target, f"💠 Chưởng Môn Nhân đã điều chỉnh Điểm Cống Hiến của đạo hữu ({'+' if n > 0 else ''}{n}).")
        msg = f"Đã điều chỉnh {n:+} điểm cho [{target['username']}]."
    elif action == "add_stone":
        loai, _, sl = str(value or "").partition(":")
        if loai not in STONE_TYPES:
            return jsonify({"success": False, "message": "Định dạng linh thạch: loai:so_luong (nguyen/vien/vi/vinh)."})
        n = int(sl or 0)
        _add_stone(target, loai, n)
        _notify(target, f"💎 Chưởng Môn Nhân đã tặng đạo hữu {n} {STONE_TYPES[loai]['name']}.")
        msg = f"Đã tặng {n} {STONE_TYPES[loai]['name']} cho [{target['username']}]."
    elif action == "set_capacity":
        g["capacity"] = int(value)
        msg = f"Đã đặt dung lượng túi của [{target['username']}] = {value} (-1 là không giới hạn)."
    else:
        return jsonify({"success": False, "message": "Hành động không hợp lệ!"})
    save_database(db, f"Admin @{admin['username']} action {action} -> @{target['username']}")
    return jsonify({"success": True, "message": msg})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
