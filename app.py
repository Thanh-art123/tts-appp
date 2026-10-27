import os
import re
from html import unescape as _html_unescape
import json
import base64
import threading
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, Response

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
    data.setdefault("market", {"seq": 1, "listings": [], "auctions": [], "barters": [], "deals": []})
    data.setdefault("events", {"tuan_le": {"active": False, "ends": 0},
                               "dai_hoi": {"active": False, "ends": 0}})
    # Đợt 7: Bảng Tin Tông Môn + Chat Đệ Tử ở trang chủ
    if "news" not in data:
        data["news"] = [{"id": 1, "ts": 0, "time": "", "loai": "he_thong",
                         "text": "📰 Bảng Tin Tông Môn chính thức khai trương! Sự kiện, cống hiến, thăng cấp của đệ tử và thông báo của Chưởng Môn Nhân sẽ hiện lên bảng này."}]
    data.setdefault("chat", [])
    # Đợt 8: bản thảo truyện chờ Tông Chủ duyệt mới được đăng lên Tàng Kinh Các
    data.setdefault("submissions", [])
    # Đợt 9: nạp tiền vào Ví (yên) — lệnh nạp chờ duyệt, lần rút về tài khoản, cài đặt
    data.setdefault("topups", [])
    data.setdefault("payouts", [])
    data.setdefault("settings", {"bank_info": ""})
    for user in data["users"]:
        if "tu_luyen" not in user:
            user["tu_luyen"] = get_default_tu_luyen()
        if "bag" not in user or not isinstance(user["bag"], list):
            user["bag"] = []
        # Đợt 1 (web truyện + game): bổ sung dữ liệu game cho mọi tài khoản,
        # kể cả tài khoản cũ — không mất bất kỳ dữ liệu nào đã có.
        user["game"] = _ensure_game(user)
    return data


def _norm_title(t):
    """Chuẩn hóa tên truyện để so trùng: thường hóa, gộp khoảng trắng."""
    return " ".join((t or "").strip().lower().split())


def _book_title_exists(db, title):
    """Mỗi truyện trên Tàng Kinh Các chỉ giữ 1 bản duy nhất (theo tên)."""
    nt = _norm_title(title)
    return any(_norm_title(b.get("title")) == nt for b in db.get("books", []))


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
    # --- Nghề phụ trợ (làm một lần, tiến độ tính tổng) ---
    {"id": "nk_khai5", "group": "nghe", "name": "Thợ Mỏ Tập Sự", "desc": "Khai thác 5 lần ở Mỏ Quặng (Nghề Phụ Trợ)", "counter": "khai_thac", "target": 5, "reward": {"diem": 30, "exp": 15}},
    {"id": "nk_cau5", "group": "nghe", "name": "Ngư Ông Tập Sự", "desc": "Câu cá 5 lần ở Đầm Câu (Nghề Phụ Trợ)", "counter": "cau_ca", "target": 5, "reward": {"diem": 30, "exp": 15}},
    {"id": "nk_phu3", "group": "nghe", "name": "Phù Sư Tập Sự", "desc": "Hoàn thành 3 lá phù ở Luyện Phù Các", "counter": "ve_phu_ok", "target": 3, "reward": {"diem": 45, "exp": 20}},
    {"id": "nk_lk1", "group": "nghe", "name": "Khí Sư Tập Sự", "desc": "Luyện khí thành công 1 pháp khí ở Luyện Khí Các", "counter": "lk_ok", "target": 1, "reward": {"diem": 40, "exp": 20}},
    {"id": "nk_dan1", "group": "nghe", "name": "Đan Sư Tập Sự", "desc": "Luyện thành công 1 viên đan ở Luyện Đan Các", "counter": "ld_ok", "target": 1, "reward": {"diem": 40, "exp": 20}},
    {"id": "nk_thuhoach2", "group": "nghe", "name": "Dược Nông Tập Sự", "desc": "Thu hoạch 2 lần ở Linh Dược Các", "counter": "thu_hoach", "target": 2, "reward": {"diem": 30, "exp": 15}},
    {"id": "nk_thu1", "group": "nghe", "name": "Ngự Thú Sư", "desc": "Sở hữu 1 Linh Thú (câu được ở Đầm Câu hoặc mua Trứng Linh Thú ở Ngự Thú Các)", "counter": "co_thu", "target": 1, "reward": {"diem": 60, "exp": 30}},
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
        "vi_yen": 0,
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
        "nghe": {"quang": {}, "dan": {"hoi_linh": 0, "tu_linh": 0},
                 "ca": {"thuong": 0, "ngon": 0, "linh_ngu": 0},
                 "phap_khi": [], "thu": None,
                 "vuon": [None, None, None, None], "luot_tao_anh": 0, "phu_open": False},
        "locked": False,
        "chibi": None,
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


def _post_news(db, loai, text):
    """Đợt 7: ghi 1 tin lên Bảng Tin Tông Môn (trang chủ). Chỉ ghi vào db —
    nơi gọi chịu trách nhiệm save_database như bình thường."""
    news = db.setdefault("news", [])
    nid = (int(news[-1].get("id", 0)) + 1) if news else 1
    news.append({"id": nid, "ts": time.time(), "time": now_jst(), "loai": loai, "text": text})
    db["news"] = news[-100:]


def _notify_admins(db, text):
    """Đợt 8: gửi chuông cho mọi tài khoản admin (Tông Chủ)."""
    for u in db.get("users", []):
        if u.get("role") == "admin":
            _notify(u, text)


def _new_submission(db, user, title, content, cat, elem, source, bag_item_id=None, phi=0):
    """Đợt 8: tạo bản thảo truyện chờ duyệt. source: 'upload' | 'bag'."""
    subs = db.setdefault("submissions", [])
    sid = (int(subs[-1].get("id", 0)) + 1) if subs else 1
    sub = {"id": sid, "ts": time.time(), "time": now_jst(),
           "username": user["username"], "author_name": user.get("author_name", user["username"]),
           "title": title, "content": content, "category": cat, "element": elem,
           "source": source, "bag_item_id": bag_item_id, "phi_nguyen": int(phi),
           "status": "pending", "reject_reason": ""}
    subs.append(sub)
    db["submissions"] = subs[-150:]
    return sub


def _pending_title_exists(db, title):
    nt = _norm_title(title)
    return any(s.get("status") == "pending" and _norm_title(s.get("title", "")) == nt
               for s in db.get("submissions", []))


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
        "vi_yen": int(g.get("vi_yen", 0)),
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
        "chibi": g.get("chibi"),
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
    if path.startswith("/static/") or path == "/sw.js" or path in _PUBLIC_PATHS:
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


# ============================================================
# GIỌNG ĐỌC PHÍA MÁY CHỦ (edge-tts) — giọng AI thần âm chạy trên server,
# phát qua thẻ audio nên điện thoại Android / app / WebView đều nghe được,
# không phụ thuộc giọng máy của trình duyệt như trước.
# ============================================================
try:
    import asyncio as _asyncio
    import edge_tts as _edge_tts
except Exception:
    _edge_tts = None

TTS_VOICES = {"vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"}


@app.route('/api/tts', methods=['POST'])
def api_tts():
    if _edge_tts is None:
        return jsonify({"success": False, "message": "Máy chủ chưa sẵn sàng giọng đọc."}), 503
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "message": "Chưa có nội dung cần đọc."}), 400
    text = text[:1000]
    voice = data.get("voice") or "vi-VN-HoaiMyNeural"
    if voice not in TTS_VOICES:
        voice = "vi-VN-HoaiMyNeural"

    async def _gen():
        comm = _edge_tts.Communicate(text, voice)
        buf = bytearray()
        async for part in comm.stream():
            if part.get("type") == "audio":
                buf.extend(part.get("data") or b"")
        return bytes(buf)

    try:
        audio = _asyncio.run(_gen())
    except Exception as e:
        print("[TTS] Loi:", e)
        return jsonify({"success": False, "message": "Tạo giọng đọc thất bại, thử lại sau."}), 502
    if not audio:
        return jsonify({"success": False, "message": "Không tạo được âm thanh."}), 502
    return Response(audio, mimetype="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


@app.route('/sw.js')
def service_worker():
    """Service Worker phải phục vụ từ gốc (/) mới có phạm vi toàn trang cho PWA."""
    resp = send_from_directory(app.static_folder, 'sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/tang-kinh-cac')
def tang_kinh_cac():
    return render_template('tang_kinh_cac.html')


@app.route('/che-tap-cac')
def che_tap_cac():
    return render_template('che_tap_cac.html')


@app.route('/nghe-phu-tro')
def nghe_phu_tro():
    return render_template('che_tap_cac.html')


@app.route('/dong-phu')
def dong_phu():
    return render_template('dong_phu.html')


@app.route('/tui-tru-vat')
def tui_tru_vat():
    return render_template('tui_tru_vat.html')


@app.route('/vi-tien')
def vi_tien():
    # Đợt 12: đã hủy tính năng nạp tiền — đường cũ /vi-tien chuyển về Chợ Đấu Giá
    # (nơi có dòng nhắn: muốn đổi điểm bằng ngân tệ thì nhắn trực tiếp với Tông Chủ).
    return redirect('/bi-canh')


@app.route('/bi-canh')
def bi_canh():
    return render_template('bi_canh.html')


@app.route('/cho-dau-gia')
def cho_dau_gia():
    return render_template('bi_canh.html')


_CHAP_HEAD = re.compile(r'^\s*(Chương|Chapter|Phần|Hồi)\s+\d+', re.I)


def _strip_html(text):
    """Bóc toàn bộ thẻ HTML khỏi nội dung truyện (nhiều truyện tải lên là file
    HTML/EPUB nguyên bản) để lấy chữ thuần trước khi chia đoạn/chương."""
    t = re.sub(r'(?is)<(script|style|head).*?>.*?</\1>', '', text)
    t = re.sub(r'(?i)<br\s*/?>', '\n', t)
    t = re.sub(r'(?i)</(p|div|h[1-6]|li|tr|section|article)>', '\n', t)
    t = re.sub(r'<[^>]+>', '', t)
    return _html_unescape(t)


def _prepare_text(content):
    """Làm sạch nội dung gốc: bóc HTML nếu có, và bỏ qua cụm MỤC LỤC dày đặc
    ở đầu (danh sách 'Chương N' san sát nhau) để tới thẳng chương thật."""
    text = content or ''
    head = text[:4000].lower()
    if '<html' in head or '<body' in head or '<p>' in head or '<div' in head or '<br' in head or '<a ' in head:
        text = _strip_html(text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Chỉ xét tiêu đề ở ĐẦU DÒNG; cụm mục lục = nhiều tiêu đề đầu dòng san sát
    heads = [m.start() for m in re.finditer(r'(?im)^\s*(?:Chương|Chapter)\s+\d+', text)]
    if len(heads) >= 6:
        for i in range(len(heads)):
            nxt = heads[i + 1] if i + 1 < len(heads) else len(text)
            if nxt - heads[i] > 500:  # tiêu đề này có thân bài thật phía sau
                dense = sum(1 for j in range(i) if heads[j + 1] - heads[j] <= 200)
                if dense >= 3:  # phía trước là cụm mục lục dày -> cắt bỏ
                    text = text[heads[i]:]
                break
    return text


def _paragraphs(content):
    """Tách nội dung truyện thành từng đoạn ngay trên máy chủ — trang đọc
    hiển thị tức thì bằng HTML thuần, không phụ thuộc JS xử lý trên máy khách
    (nguyên nhân cũ khiến truyện kẹt ở dòng 'Đang xử lý...' trên điện thoại)."""
    text = _prepare_text(content)
    text = re.sub(r'([^\n])((?:Chương|Chapter)\s+\d+[:\s])', r'\1\n\2', text)
    paras, chap = [], 0
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        is_chap = bool(_CHAP_HEAD.match(line))
        if is_chap:
            chap += 1
        paras.append({"t": line, "chap": chap if is_chap else 0,
                        "anchor": f"chap-pos-{chap}" if is_chap else ""})
    return paras


def _split_chapters(content):
    """Chia truyện thành các chương theo tiêu đề 'Chương N' để nghe từng tập."""
    chapters, cur = [], None
    for p in _paragraphs(content):
        if p["chap"]:
            cur = {"title": p["t"][:120], "paras": []}
            chapters.append(cur)
        else:
            if cur is None:
                cur = {"title": "Mở đầu", "paras": []}
                chapters.append(cur)
            cur["paras"].append(p["t"])
    for c in chapters:
        c["text"] = "\n".join(c.pop("paras"))
    if not chapters:
        chapters = [{"title": "Toàn văn", "text": (content or '').strip()}]
    return chapters


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
    paras = _paragraphs(content)
    links = [p for p in paras if p["chap"]]
    return render_template('doc_truyen.html', chapter_title=title,
                           paragraphs=paras, chapter_links=links)


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
    cb = _chibi_valid(data.get('chibi'))
    if cb:
        new_user["game"]["chibi"] = cb
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


@app.route('/api/nghe/books', methods=['GET'])
def nghe_books():
    """Danh sách truyện nhẹ (không kèm nội dung) cho mục Nghe Truyện."""
    db = load_database()
    out = []
    for b in db['books']:
        out.append({
            "id": b.get('id'), "title": b.get('title', ''),
            "author": b.get('author', ''), "username": b.get('username', ''),
            "chapters": len(_split_chapters(b.get('content', '')))
        })
    return jsonify({"success": True, "books": out})


@app.route('/api/nghe/book/<int:bid>', methods=['GET'])
def nghe_book(bid):
    """Một truyện đã chia sẵn theo chương để máy nghe đọc nối tiếp."""
    db = load_database()
    b = next((x for x in db['books'] if x.get('id') == bid), None)
    if not b:
        return jsonify({"success": False, "message": "Không tìm thấy truyện."}), 404
    return jsonify({"success": True, "id": b.get('id'), "title": b.get('title', ''),
                    "chapters": _split_chapters(b.get('content', ''))})


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
    if _book_title_exists(db, title):
        return jsonify({"success": False, "message": f"❌ Truyện [{title}] đã có trên Tàng Kinh Các rồi — mỗi truyện chỉ giữ 1 bản duy nhất, không đăng trùng nữa!"})
    if _pending_title_exists(db, title):
        return jsonify({"success": False, "message": f"Truyện [{title}] đang có bản thảo chờ Tông Chủ duyệt rồi — vui lòng đợi duyệt hoặc chọn tên khác!"})

    # Đợt 8: đệ tử thường phải gửi BẢN THẢO cho Tông Chủ duyệt mới được đăng.
    # Chỉ tài khoản admin (Tông Chủ) mới đăng thẳng lên Tàng Kinh Các.
    poster = find_user(db, username)
    if poster and poster.get('role') != 'admin':
        _new_submission(db, poster, title, content, cat, elem, "upload")
        _notify(poster, f"📝 Bản thảo truyện «{title}» đã gửi Tông Chủ duyệt. Được duyệt, truyện sẽ lên Tàng Kinh Các và đạo hữu nhận +50 Điểm Cống Hiến.")
        _notify_admins(db, f"📝 Có bản thảo truyện mới chờ duyệt: «{title}» của {poster.get('author_name', username)}.")
        save_database(db, f"@{username} gui ban thao truyen: {title}")
        return jsonify({"success": True, "pending": True, "message": f"📝 Đã gửi bản thảo truyện [{title}] cho Tông Chủ duyệt! Được duyệt, truyện sẽ xuất hiện trên Tàng Kinh Các và đạo hữu nhận +50 Điểm Cống Hiến."})

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

    _post_news(db, "truyen", f"📚 {author} vừa cống hiến truyện mới «{title}» lên Tàng Kinh Các!")
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


def _chuyen_kiem_tra(db, user, item):
    """Mục KIỂM TRA trước khi chuyển truyện từ Túi Trữ Vật lên Tàng Kinh Các.

    Quy định của ông chủ: phí chuyển = số chương × 1 Nguyên Tinh
    (Nguyên Tinh là linh thạch 10 điểm linh khí; vd 100 chương = 100 Nguyên Tinh).
    Không đủ chương (truyện trống) hoặc không đủ Nguyên Tinh -> báo lỗi rõ ràng.
    Mỗi truyện trên Tàng Kinh Các chỉ giữ 1 bản duy nhất.
    """
    content = (item.get("content") or "").strip()
    so_chuong = len(_split_chapters(item.get("content") or "")) if content else 0
    g = _ensure_game(user)
    dang_co = int(g.get("linh_thach", {}).get("nguyen", 0))
    phi = so_chuong  # 1 chương = 1 Nguyên Tinh
    loi = None
    if so_chuong < 1:
        loi = "Truyện chưa có chương nào (nội dung trống) — không chuyển được!"
    elif item.get("published_book_id") and any(b.get('id') == item["published_book_id"] for b in db['books']):
        loi = f"Truyện [{item['title']}] đã có trên Tàng Kinh Các rồi!"
    elif _book_title_exists(db, item["title"]):
        loi = f"Trên Tàng Kinh Các đã có 1 bản truyện [{item['title']}] — mỗi truyện chỉ giữ 1 bản duy nhất, không chuyển trùng nữa!"
    elif dang_co < phi:
        loi = (f"Không đủ Nguyên Tinh! Truyện có {so_chuong} chương nên phí chuyển là {phi} Nguyên Tinh, "
               f"đạo hữu đang có {dang_co} (thiếu {phi - dang_co}). Kiếm Nguyên Tinh từ Nhiệm Vụ Tông Môn "
               f"hoặc mua tại Sảnh Chính (10 Điểm Cống Hiến/viên).")
    return {"so_chuong": so_chuong, "phi_nguyen_tinh": phi, "dang_co": dang_co,
            "du": loi is None, "loi": loi}


@app.route('/api/bag/kiem-tra-chuyen', methods=['GET'])
def api_bag_kiem_tra_chuyen():
    """Cho giao diện hiển thị mục kiểm tra (số chương, phí, số dư) trước khi ấn Chuyển."""
    username = request.args.get('username', '').strip()
    item_id = request.args.get('item_id')
    db = load_database()
    user = find_user(db, username)
    if not user:
        return jsonify({"success": False, "message": "Cần đăng nhập để dùng Túi Trữ Vật!"})
    item = _find_bag_item(user, item_id)
    if not item:
        return jsonify({"success": False, "message": "Không tìm thấy tài nguyên trong túi!"})
    kt = _chuyen_kiem_tra(db, user, item)
    return jsonify({"success": True, "title": item["title"], **kt})


@app.route('/api/bag/chuyen', methods=['POST'])
@app.route('/api/bag/publish', methods=['POST'])
def api_bag_chuyen():
    """CHUYỂN một tài nguyên từ Túi Trữ Vật sang Tàng Kinh Các (truyện rời túi).

    Phí: 1 chương = 1 Nguyên Tinh. Máy chủ kiểm tra lại toàn bộ (số chương,
    trùng bản, số dư Nguyên Tinh) trước khi trừ phí — thiếu là báo lỗi, không trừ.
    """
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

    kt = _chuyen_kiem_tra(db, user, item)
    if not kt["du"]:
        return jsonify({"success": False, "message": "❌ Không chuyển được: " + kt["loi"], "kiem_tra": kt})

    cat = cat or item.get("category", "truyen-dich")
    elem = elem or item.get("element", "Kim")
    phi = kt["phi_nguyen_tinh"]
    so_chuong = kt["so_chuong"]

    # Đợt 8: đệ tử thường chuyển truyện từ Túi lên cũng phải qua duyệt bản thảo.
    # Phí Nguyên Tinh trừ lúc gửi như cũ; bị từ chối sẽ hoàn lại, truyện vẫn ở trong Túi.
    # Chỉ admin (Tông Chủ) mới chuyển thẳng.
    if user.get("role") != "admin":
        if _pending_title_exists(db, item["title"]) or any(
                s.get("status") == "pending" and s.get("bag_item_id") == item["item_id"]
                and s.get("username") == username for s in db.get("submissions", [])):
            return jsonify({"success": False, "message": "Truyện này đang có bản thảo chờ Tông Chủ duyệt rồi — vui lòng đợi duyệt!", "kiem_tra": kt})
        _add_stone(user, "nguyen", -phi)
        _log(user, f"Gửi bản thảo chuyển truyện [{item['title']}] chờ Tông Chủ duyệt: {so_chuong} chương, phí -{phi} Nguyên Tinh (hoàn lại nếu bị từ chối)")
        _new_submission(db, user, item["title"], item.get("content", ""), cat, elem, "bag",
                        bag_item_id=item["item_id"], phi=phi)
        _notify(user, f"📝 Đã gửi bản thảo truyện «{item['title']}» chờ Tông Chủ duyệt (phí {phi} Nguyên Tinh đã trừ — bị từ chối sẽ hoàn lại, truyện vẫn nằm trong Túi).")
        _notify_admins(db, f"📝 Bản thảo chuyển truyện chờ duyệt: «{item['title']}» của {user.get('author_name', username)} (từ Túi Trữ Vật).")
        save_database(db, f"@{username} gui ban thao chuyen truyen: {item['title']} ({so_chuong} chuong, phi {phi} NT)")
        return jsonify({"success": True, "pending": True, "kiem_tra": kt,
                        "message": f"📝 Đã gửi bản thảo truyện [{item['title']}] cho Tông Chủ duyệt! Phí {phi} Nguyên Tinh đã trừ (bị từ chối sẽ hoàn lại). Truyện vẫn nằm trong Túi Trữ Vật — được duyệt mới chuyển hẳn lên Tàng Kinh Các."})

    _add_stone(user, "nguyen", -phi)
    _log(user, f"Chuyển truyện [{item['title']}] từ Túi Trữ Vật lên Tàng Kinh Các: {so_chuong} chương × 1 Nguyên Tinh = -{phi} Nguyên Tinh")

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
    # CHUYỂN = truyện rời Túi Trữ Vật, sang hẳn Tàng Kinh Các
    user["bag"] = [i for i in user["bag"] if i["item_id"] != item["item_id"]]

    user.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50
    _add_diem(user, 50, f"Chuyển truyện [{item['title']}] từ Túi lên Tàng Kinh Các")
    _add_exp(user, 50)
    _bump(user, "dang_truyen")
    _post_news(db, "truyen", f"📚 {user.get('author_name', username)} vừa cống hiến truyện «{item['title']}» từ Túi Trữ Vật lên Tàng Kinh Các!")
    save_database(db, f"@{username} chuyen truyen tu tui len Tang Kinh Cac: {item['title']} ({so_chuong} chuong, phi {phi} NT)")
    return jsonify({"success": True,
                    "message": f"🔀 Đã chuyển truyện [{item['title']}] lên Tàng Kinh Các (mục {cat} - hệ {elem}): {so_chuong} chương, phí {phi} Nguyên Tinh. Truyện đã rời Túi Trữ Vật. +50 Điểm Cống Hiến.",
                    "book": new_book, "kiem_tra": kt})


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


# --- Nhân vật chibi (Đợt 5): mỗi tài khoản 1 nhân vật, gắn vĩnh viễn với danh xưng ---
CHIBI_PHAP_BAO = ("kiem", "quat", "ho_lo", "sach", "truong", "khong")


def _chibi_valid(c):
    """Kiểm tra & chuẩn hóa cấu hình chibi {toc 0-5, mau_toc 0-5, ao 0-7, phap_bao}."""
    if not isinstance(c, dict):
        return None
    try:
        toc = int(c.get("toc", 0))
        mau_toc = int(c.get("mau_toc", 0))
        ao = int(c.get("ao", 0))
    except (TypeError, ValueError):
        return None
    pb = c.get("phap_bao", "sach")
    if not (0 <= toc <= 5 and 0 <= mau_toc <= 5 and 0 <= ao <= 7 and pb in CHIBI_PHAP_BAO):
        return None
    return {"toc": toc, "mau_toc": mau_toc, "ao": ao, "phap_bao": pb}


@app.route('/api/chibi/chon', methods=['POST'])
def api_chibi_chon():
    """Chọn nhân vật chibi — chỉ được chọn 1 lần duy nhất cho mỗi tài khoản."""
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    if g.get("chibi"):
        return jsonify({"success": False, "message": "Đạo hữu đã có nhân vật chibi gắn với danh xưng rồi! Mỗi tài khoản chỉ có 1 nhân vật duy nhất — muốn nhân vật khác, hãy tạo tài khoản mới."})
    cb = _chibi_valid((request.json or {}).get('chibi'))
    if not cb:
        return jsonify({"success": False, "message": "Thông tin nhân vật chưa hợp lệ (kiểu tóc / màu tóc / áo / pháp bảo)!"})
    g["chibi"] = cb
    _notify(user, "🧒 Nhân vật chibi đã gắn vĩnh viễn với danh xưng của đạo hữu. Chúc đạo hữu tu luyện vui vẻ tại Đại Thiên Tông!")
    save_database(db, f"@{user['username']} chon nhan vat chibi")
    return jsonify({"success": True, "message": "🧒 Đã chốt nhân vật chibi — nhân vật này gắn vĩnh viễn với danh xưng của đạo hữu!", "game": game_summary(user)})


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
    thu = (g.get("nghe") or {}).get("thu")
    if thu:
        lv = _beast_level(thu)
        bonus = lv * 3
        _add_diem(user, bonus, f"Linh thú {thu.get('ten', '')} sản xuất khi điểm danh")
        msgs.append(f"Linh thú {thu.get('ten', '')} (cấp {lv}) sản xuất thêm +{bonus} Điểm Cống Hiến!")
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
        _post_news(db, "thang_cap", f"⬆️ THĂNG CẤP: {user.get('author_name', user['username'])} vừa đột phá lên cảnh giới {REALM_NAMES[muc]} · Nhập Môn! Toàn Tông Môn chúc mừng đạo hữu! 🎉")
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
    if m["group"] in ("tan_thu", "nghe"):
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
    if m["group"] == "nghe":
        if m["id"] in ms.get("nghe_done", []):
            return "done"
        return "current"
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
    return jsonify({"success": True, "missions": out, "game": game_summary(user),
                    "events": _events_view(db)})


@app.route('/api/missions/nhan', methods=['POST'])
def api_missions_nhan():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _mission_def((request.json or {}).get('id'))
    if not m:
        return jsonify({"success": False, "message": "Nhiệm vụ không tồn tại!"})
    if m["group"] in ("tan_thu", "nghe"):
        return jsonify({"success": False, "message": "Nhiệm vụ này luôn sẵn sàng, không cần nhận!"})
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
    x2 = _event_on(db, "tuan_le")  # Đợt 4: Tuần Lễ Tông Môn x2 Điểm & EXP nhiệm vụ
    he_so = 2 if x2 else 1
    if rw.get("diem"):
        gain = int(round(rw["diem"] * he_so * (1 + (_treasure_bonus_diem(user) + _beast_bonus_pct(user)) / 100.0)))
        _add_diem(user, gain, f"Thưởng nhiệm vụ [{m['name']}]" + (" (Tuần Lễ Tông Môn x2)" if x2 else ""))
        parts.append(f"+{gain} Điểm Cống Hiến")
    if rw.get("exp"):
        _add_exp(user, rw["exp"] * he_so)
        parts.append(f"+{rw['exp'] * he_so} Kinh Nghiệm")
    for loai, n in (rw.get("stones") or {}).items():
        _add_stone(user, loai, n)
        parts.append(f"+{n} {STONE_TYPES[loai]['name']}")
    ms = g["missions"]
    if m["group"] == "tan_thu":
        ms.setdefault("newbie_done", []).append(m["id"])
    elif m["group"] == "nghe":
        ms.setdefault("nghe_done", []).append(m["id"])
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
    return jsonify({"success": True, "message": f"Hoàn thành [{m['name']}]: " + ", ".join(parts) + "." + (" 🎉 Tuần Lễ Tông Môn: Điểm & Kinh Nghiệm nhiệm vụ được x2!" if x2 else ""), "game": game_summary(user)})


# --- Nghề Phụ Trợ (Đợt 2) ---
BEAST_NAMES = ["Tiểu Bạch Hổ", "Hắc Kỳ Lân Con", "Thanh Loan", "Hỏa Hồ Ly",
               "Ngọc Thố", "Kim Sí Điểu", "Huyền Quy Nhỏ", "Tuyết Lang Con"]
ORE_WEIGHTS = [(1, 30), (2, 24), (3, 18), (4, 12), (5, 7), (6, 4), (7, 2.5), (8, 1.5), (9, 1)]


def _nghe(user):
    g = _ensure_game(user)
    ng = g.get("nghe")
    if not isinstance(ng, dict):
        ng = default_game(user)["nghe"]
        g["nghe"] = ng
    for k, v in default_game(user)["nghe"].items():
        if k not in ng:
            ng[k] = v
    return ng


def _beast_level(thu):
    if not thu:
        return 0
    return min(10, int(thu.get("exp", 0)) // 3 + 1)


def _beast_bonus_pct(user):
    thu = (_ensure_game(user).get("nghe") or {}).get("thu")
    return _beast_level(thu) * 2 if thu else 0


def _today_count(user, key):
    g = _ensure_game(user)
    return int(g["stats"].get("today", {}).get(key, 0))


def _nghe_gain(user, base, reason):
    """Cộng điểm cho hoạt động nghề, kèm % cộng thêm của Linh Thú."""
    gain = int(round(int(base) * (1 + _beast_bonus_pct(user) / 100.0)))
    _add_diem(user, gain, reason)
    return gain


def _nghe_view(user):
    g = _ensure_game(user)
    ng = _nghe(user)
    now = time.time()
    vuon = []
    for plot in ng.get("vuon", []):
        if not plot:
            vuon.append(None)
        else:
            vuon.append({"cap": plot["cap"], "con_lai": max(0, int(plot["ready"] - now))})
    thu = ng.get("thu")
    return {
        "quang": ng.get("quang", {}),
        "dan": ng.get("dan", {}),
        "ca": ng.get("ca", {}),
        "phap_khi": ng.get("phap_khi", []),
        "thu": ({"ten": thu["ten"], "exp": thu.get("exp", 0), "cap": _beast_level(thu),
                 "bonus_pct": _beast_bonus_pct(user)} if thu else None),
        "vuon": vuon,
        "luot_tao_anh": ng.get("luot_tao_anh", 0),
        "phu_open": bool(ng.get("phu_open")),
        "hom_nay": {"khai_thac": _today_count(user, "khai_thac"),
                    "cau_ca": _today_count(user, "cau_ca"),
                    "ve_phu": _today_count(user, "ve_phu")},
        "herbs": g["inventory"].get("herbs", {}),
        "linh_khi": g.get("linh_khi", 0),
    }


def _nghe_resp(user, message):
    return jsonify({"success": True, "message": message, "game": game_summary(user), "nghe": _nghe_view(user)})


@app.route('/api/nghe', methods=['GET'])
def api_nghe():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    return jsonify({"success": True, "game": game_summary(user), "nghe": _nghe_view(user)})


@app.route('/api/nghe/khai-thac', methods=['POST'])
def api_nghe_khai_thac():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if _today_count(user, "khai_thac") >= 10:
        return jsonify({"success": False, "message": "Hôm nay đã khai thác đủ 10 lượt ở Mỏ Quặng rồi, mai quay lại nhé!"})
    ng = _nghe(user)
    tiers = [t for t, w in ORE_WEIGHTS]
    weights = [w for t, w in ORE_WEIGHTS]
    tier = random.choices(tiers, weights=weights, k=1)[0]
    ng["quang"][str(tier)] = int(ng["quang"].get(str(tier), 0)) + 1
    _bump(user, "khai_thac")
    gain = _nghe_gain(user, 2, "Khai thác quặng ở Mỏ Quặng")
    _add_exp(user, 3)
    save_database(db, f"@{user['username']} khai thac duoc quang cap {tier}")
    return _nghe_resp(user, f"⛏️ Khai thác thành công: nhận 1 Quặng Cấp {tier} (+{gain} điểm, +3 EXP). Quặng dùng để luyện khí ở Luyện Khí Các.")


@app.route('/api/nghe/luyen-khi', methods=['POST'])
def api_nghe_luyen_khi():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    tier = int((request.json or {}).get('cap', 0) or 0)
    if tier < 1 or tier > 9:
        return jsonify({"success": False, "message": "Cấp quặng không hợp lệ (1-9)!"})
    ng = _nghe(user)
    if int(ng["quang"].get(str(tier), 0)) < 1:
        return jsonify({"success": False, "message": f"Chưa có Quặng Cấp {tier}! Hãy khai thác ở Mỏ Quặng trước."})
    g = _ensure_game(user)
    if int(g["linh_thach"].get("nguyen", 0)) < 1:
        return jsonify({"success": False, "message": "Luyện khí cần 1 Nguyên Tinh làm lõi! Mua ở Sảnh Chính nhé."})
    ng["quang"][str(tier)] -= 1
    _add_stone(user, "nguyen", -1)
    rate = max(40, 95 - 6 * (tier - 1))
    if random.randint(1, 100) <= rate:
        ng["phap_khi"].append(tier)
        _bump(user, "lk_ok")
        gain = _nghe_gain(user, 10 + 5 * tier, f"Luyện khí thành công pháp khí cấp {tier}")
        _add_exp(user, 10)
        save_database(db, f"@{user['username']} luyen khi thanh cong cap {tier}")
        return _nghe_resp(user, f"🔨 Luyện khí THÀNH CÔNG (tỉ lệ {rate}%)! Nhận Pháp Khí Cấp {tier} (+{gain} điểm, +10 EXP). Bán pháp khí cũng ra điểm đấy.")
    gain = _nghe_gain(user, 3, "Luyện khí thất bại, bán phế phẩm")
    _add_exp(user, 3)
    save_database(db, f"@{user['username']} luyen khi that bai cap {tier}")
    return _nghe_resp(user, f"💥 Luyện khí THẤT BẠI (tỉ lệ thành công {rate}%)! Quặng và lõi đã mất, bán phế phẩm được +{gain} điểm (+3 EXP).")


@app.route('/api/nghe/ban-phap-khi', methods=['POST'])
def api_nghe_ban_phap_khi():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    tier = int((request.json or {}).get('cap', 0) or 0)
    ng = _nghe(user)
    if tier not in ng["phap_khi"]:
        return jsonify({"success": False, "message": "Không có pháp khí cấp này trong kho!"})
    ng["phap_khi"].remove(tier)
    gain = tier * tier * 15
    _add_diem(user, gain, f"Bán Pháp Khí Cấp {tier}")
    save_database(db, f"@{user['username']} ban phap khi cap {tier}")
    return _nghe_resp(user, f"Đã bán Pháp Khí Cấp {tier}, nhận +{gain} Điểm Cống Hiến.")


@app.route('/api/nghe/luyen-dan', methods=['POST'])
def api_nghe_luyen_dan():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    loai = (request.json or {}).get('loai')
    g = _ensure_game(user)
    herbs = g["inventory"].setdefault("herbs", {})
    if loai == "hoi_linh":
        need, rate, ten = {"1": 2, "2": 1}, 85, "Hồi Linh Đan"
    elif loai == "tu_linh":
        need, rate, ten = {"3": 2, "4": 1}, 70, "Tụ Linh Đan"
    else:
        return jsonify({"success": False, "message": "Loại đan không hợp lệ!"})
    for gr, n in need.items():
        if int(herbs.get(gr, 0)) < n:
            return jsonify({"success": False, "message": f"Thiếu Thảo Mộc Cấp {gr} (cần {n})! Trồng ở Linh Dược Các hoặc mua ở Sảnh Chính."})
    for gr, n in need.items():
        herbs[gr] = int(herbs.get(gr, 0)) - n
    ng = _nghe(user)
    if random.randint(1, 100) <= rate:
        ng["dan"][loai] = int(ng["dan"].get(loai, 0)) + 1
        _bump(user, "ld_ok")
        _add_exp(user, 10)
        save_database(db, f"@{user['username']} luyen dan {loai} thanh cong")
        return _nghe_resp(user, f"⚗️ Luyện đan THÀNH CÔNG (tỉ lệ {rate}%)! Nhận 1 viên {ten} (+10 EXP). Vào Luyện Đan Các để dùng đan, +Linh Khí trực tiếp.")
    _add_exp(user, 2)
    save_database(db, f"@{user['username']} luyen dan {loai} that bai")
    return _nghe_resp(user, f"💥 Luyện đan THẤT BẠI (tỉ lệ {rate}%)! Nguyên liệu đã mất, nhận +2 EXP kinh nghiệm.")


@app.route('/api/nghe/dung-dan', methods=['POST'])
def api_nghe_dung_dan():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    loai = (request.json or {}).get('loai')
    conf = {"hoi_linh": (40, "Hồi Linh Đan"), "tu_linh": (200, "Tụ Linh Đan")}
    if loai not in conf:
        return jsonify({"success": False, "message": "Loại đan không hợp lệ!"})
    ng = _nghe(user)
    if int(ng["dan"].get(loai, 0)) < 1:
        return jsonify({"success": False, "message": "Chưa có viên đan này trong kho!"})
    ng["dan"][loai] -= 1
    lk, ten = conf[loai]
    g = _ensure_game(user)
    g["linh_khi"] = int(g.get("linh_khi", 0)) + lk
    _log(user, f"Dùng {ten} (+{lk} Linh Khí)")
    save_database(db, f"@{user['username']} dung dan {loai}")
    return _nghe_resp(user, f"💊 Đã dùng 1 viên {ten}: +{lk} Linh Khí! Linh Khí hiện có: {g['linh_khi']}.")


@app.route('/api/nghe/phu/bat-dau', methods=['POST'])
def api_nghe_phu_bat_dau():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if _today_count(user, "ve_phu") >= 5:
        return jsonify({"success": False, "message": "Hôm nay đã dùng hết 5 lượt vẽ phù rồi, mai quay lại nhé!"})
    ng = _nghe(user)
    ng["phu_open"] = True
    _bump(user, "ve_phu")
    save_database(db, f"@{user['username']} bat dau ve phu")
    return _nghe_resp(user, "🖌️ Bắt đầu vẽ phù! Hãy vẽ kín khung rồi bấm Hoàn Phù. Lưu ý: bỏ ngang (bắt đầu lượt mới / rời đi) là mất lượt này.")


@app.route('/api/nghe/phu/hoan-thanh', methods=['POST'])
def api_nghe_phu_hoan_thanh():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    ng = _nghe(user)
    if not ng.get("phu_open"):
        return jsonify({"success": False, "message": "Chưa bắt đầu lượt vẽ nào! Bấm 'Bắt Đầu Vẽ Phù' trước."})
    strokes = int((request.json or {}).get('diem_ve', 0) or 0)
    if strokes < 25:
        return jsonify({"success": False, "message": f"Lá phù còn quá sơ sài ({strokes}/25 nét chấm bút). Hãy vẽ thêm cho kín khung rồi bấm Hoàn Phù."})
    ng["phu_open"] = False
    ng["luot_tao_anh"] = int(ng.get("luot_tao_anh", 0)) + 1
    _bump(user, "ve_phu_ok")
    gain = _nghe_gain(user, 15, "Hoàn thành lá phù ở Luyện Phù Các")
    _add_exp(user, 10)
    save_database(db, f"@{user['username']} hoan thanh la phu")
    return _nghe_resp(user, f"🎉 Hoàn thành lá phù! +{gain} Điểm Cống Hiến, +10 EXP và +1 Lượt Tạo Ảnh (đang có {ng['luot_tao_anh']} lượt).")


@app.route('/api/nghe/phu/tao-anh', methods=['POST'])
def api_nghe_phu_tao_anh():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    ng = _nghe(user)
    if int(ng.get("luot_tao_anh", 0)) < 1:
        return jsonify({"success": False, "message": "Chưa có Lượt Tạo Ảnh! Hãy vẽ phù ở Luyện Phù Các: hoàn thành 1 lá phù = 1 lượt tạo ảnh."})
    ng["luot_tao_anh"] -= 1
    save_database(db, f"@{user['username']} dung 1 luot tao anh")
    return _nghe_resp(user, "Đã dùng 1 Lượt Tạo Ảnh. Linh lực đang tụ, chờ ảnh hiện ra nhé!")


@app.route('/api/nghe/vuon/trong', methods=['POST'])
def api_nghe_vuon_trong():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    data = request.json or {}
    o, cap = int(data.get('o', -1)), int(data.get('cap', 0) or 0)
    ng = _nghe(user)
    if o < 0 or o > 3 or cap < 1 or cap > 9:
        return jsonify({"success": False, "message": "Ô đất hoặc cấp thảo không hợp lệ!"})
    if ng["vuon"][o]:
        return jsonify({"success": False, "message": "Ô đất này đang có cây rồi!"})
    cost = 3 * cap
    g = _ensure_game(user)
    if int(g.get("diem", 0)) < cost:
        return jsonify({"success": False, "message": f"Không đủ điểm mua hạt giống (cần {cost} điểm)!"})
    _add_diem(user, -cost, f"Mua hạt giống Thảo Mộc Cấp {cap}")
    ng["vuon"][o] = {"cap": cap, "ready": time.time() + cap * 120}
    save_database(db, f"@{user['username']} trong thao cap {cap} o {o}")
    return _nghe_resp(user, f"🌱 Đã gieo Thảo Mộc Cấp {cap} ở ô {o + 1} (-{cost} điểm). Khoảng {cap * 2} phút nữa thu hoạch được 2 cây.")


@app.route('/api/nghe/vuon/thu', methods=['POST'])
def api_nghe_vuon_thu():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    o = int((request.json or {}).get('o', -1))
    ng = _nghe(user)
    if o < 0 or o > 3 or not ng["vuon"][o]:
        return jsonify({"success": False, "message": "Ô đất này trống!"})
    plot = ng["vuon"][o]
    if time.time() < plot["ready"]:
        return jsonify({"success": False, "message": "Cây chưa lớn, chờ thêm chút nữa nhé!"})
    g = _ensure_game(user)
    herbs = g["inventory"].setdefault("herbs", {})
    herbs[str(plot["cap"])] = int(herbs.get(str(plot["cap"]), 0)) + 2
    ng["vuon"][o] = None
    _bump(user, "thu_hoach")
    _add_exp(user, 5)
    ten = HERBS[plot["cap"] - 1]["name"] if 1 <= plot["cap"] <= len(HERBS) else f"Cấp {plot['cap']}"
    save_database(db, f"@{user['username']} thu hoach thao cap {plot['cap']}")
    return _nghe_resp(user, f"🌿 Thu hoạch thành công: +2 {ten} vào kho (+5 EXP).")


@app.route('/api/nghe/cau-ca', methods=['POST'])
def api_nghe_cau_ca():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if _today_count(user, "cau_ca") >= 10:
        return jsonify({"success": False, "message": "Hôm nay đã câu đủ 10 lượt rồi, mai quay lại nhé!"})
    ng = _nghe(user)
    _bump(user, "cau_ca")
    _add_exp(user, 2)
    r = random.randint(1, 100)
    if r <= 50:
        ng["ca"]["thuong"] = int(ng["ca"].get("thuong", 0)) + 1
        msg = "🎣 Câu được 1 Cá Thường! (bán 5 điểm/con ở Đầm Câu)"
    elif r <= 72:
        ng["ca"]["ngon"] = int(ng["ca"].get("ngon", 0)) + 1
        msg = "🎣 Câu được 1 Cá Ngon! (bán 12 điểm/con)"
    elif r <= 85:
        ng["ca"]["linh_ngu"] = int(ng["ca"].get("linh_ngu", 0)) + 1
        msg = "✨ Câu được 1 LINH NGƯ hiếm! (bán 60 điểm/con)"
    elif r <= 90:
        if not ng.get("thu"):
            ten = random.choice(BEAST_NAMES)
            ng["thu"] = {"ten": ten, "exp": 0}
            _bump(user, "co_thu")
            _notify(user, f"🐾 Câu được linh thú con {ten} ở Đầm Câu! Vào Ngự Thú Các để chăm sóc.")
            msg = f"🐾✨ KHÔNG NGỜ! Câu được linh thú con: {ten}! Nó đã về Ngự Thú Các của đạo hữu (cộng % điểm + sản xuất điểm khi điểm danh)."
        else:
            _add_diem(user, 100, "Thả linh thú con câu được ở Đầm Câu, Tông Môn thưởng")
            msg = "🐾 Câu được linh thú con nhưng đạo hữu đã có linh thú rồi — thả lại hồ, Tông Môn thưởng +100 điểm!"
    else:
        msg = "🎣 Cá cắn câu hụt rồi... (vẫn +2 EXP an ủi)"
    save_database(db, f"@{user['username']} cau ca")
    return _nghe_resp(user, msg + " (+2 EXP)")


@app.route('/api/nghe/ban-ca', methods=['POST'])
def api_nghe_ban_ca():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    ng = _nghe(user)
    ca = ng["ca"]
    total = int(ca.get("thuong", 0)) * 5 + int(ca.get("ngon", 0)) * 12 + int(ca.get("linh_ngu", 0)) * 60
    if total <= 0:
        return jsonify({"success": False, "message": "Chưa có con cá nào để bán!"})
    ca["thuong"] = ca["ngon"] = ca["linh_ngu"] = 0
    _add_diem(user, total, "Bán cá ở Đầm Câu")
    save_database(db, f"@{user['username']} ban ca +{total}")
    return _nghe_resp(user, f"🐟 Đã bán hết cá, nhận +{total} Điểm Cống Hiến.")


@app.route('/api/nghe/thu/mua-trung', methods=['POST'])
def api_nghe_thu_mua_trung():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    ng = _nghe(user)
    if ng.get("thu"):
        return jsonify({"success": False, "message": "Đạo hữu đã có linh thú rồi (mỗi người 1 con)!"})
    g = _ensure_game(user)
    if int(g.get("diem", 0)) < 300:
        return jsonify({"success": False, "message": "Trứng Linh Thú giá 300 điểm — chưa đủ điểm rồi!"})
    _add_diem(user, -300, "Mua Trứng Linh Thú")
    ten = random.choice(BEAST_NAMES)
    ng["thu"] = {"ten": ten, "exp": 0}
    _bump(user, "co_thu")
    _notify(user, f"🐣 Trứng nở thành {ten}! Linh thú cộng % điểm nghề và sản xuất điểm khi điểm danh.")
    save_database(db, f"@{user['username']} mua trung linh thu {ten}")
    return _nghe_resp(user, f"🐣 Trứng nở thành {ten} (cấp 1)! Cho ăn để lên cấp: mỗi cấp +2% điểm nghề/nhiệm vụ và +3 điểm sản xuất mỗi lần điểm danh.")


@app.route('/api/nghe/thu/cho-an', methods=['POST'])
def api_nghe_thu_cho_an():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    ng = _nghe(user)
    thu = ng.get("thu")
    if not thu:
        return jsonify({"success": False, "message": "Chưa có linh thú để cho ăn!"})
    loai = (request.json or {}).get('loai')
    if loai == "ca":
        if int(ng["ca"].get("thuong", 0)) >= 1:
            ng["ca"]["thuong"] -= 1
        elif int(ng["ca"].get("ngon", 0)) >= 1:
            ng["ca"]["ngon"] -= 1
        else:
            return jsonify({"success": False, "message": "Không có cá để cho ăn! Ra Đầm Câu câu cá trước nhé."})
        thu["exp"] = min(27, int(thu.get("exp", 0)) + 2)
        msg = f"🐾 Đã cho {thu['ten']} ăn 1 con cá (+2 EXP thú)."
    elif loai == "diem":
        g = _ensure_game(user)
        if int(g.get("diem", 0)) < 20:
            return jsonify({"success": False, "message": "Cần 20 điểm để mua thức ăn cho thú!"})
        _add_diem(user, -20, f"Mua thức ăn cho {thu['ten']}")
        thu["exp"] = min(27, int(thu.get("exp", 0)) + 1)
        msg = f"🐾 Đã cho {thu['ten']} ăn (-20 điểm, +1 EXP thú)."
    else:
        return jsonify({"success": False, "message": "Loại thức ăn không hợp lệ!"})
    save_database(db, f"@{user['username']} cho linh thu an")
    return _nghe_resp(user, msg + f" Linh thú hiện cấp {_beast_level(thu)} (+{_beast_bonus_pct(user)}% điểm).")


# ============================================================
# ĐỢT 4 — SỰ KIỆN TÔNG MÔN
# - Tuần Lễ Tông Môn: Điểm & Kinh Nghiệm thưởng từ Nhiệm Vụ x2 (chạy 7 ngày).
# - Đại Hội Đấu Giá: phí sàn Chợ 5% -> 2%, giới hạn rao bán 5->10 tin,
#   phiên đấu giá 3->6, Tông Môn mở 3 lô đấu giá đặc biệt (chạy 72 giờ).
# Admin bật/tắt tại trang Quản Trị (/api/admin/event).
# ============================================================
EVENT_DEFS = {
    "tuan_le": {"name": "Tuần Lễ Tông Môn", "icon": "🎉", "hours": 24 * 7,
                "desc": "Toàn bộ Điểm Cống Hiến & Kinh Nghiệm thưởng từ Nhiệm Vụ được NHÂN ĐÔI (x2)."},
    "dai_hoi": {"name": "Đại Hội Đấu Giá", "icon": "🏆", "hours": 72,
                "desc": "Phí sàn Chợ Đấu Giá giảm còn 2%, rao bán tối đa 10 tin, mở tối đa 6 phiên đấu giá, và Tông Môn mở 3 lô đấu giá đặc biệt."},
}

# 3 lô đặc biệt do Tông Môn mở khi Đại Hội bắt đầu: (kind, key, qty, giá khởi điểm điểm)
DAI_HOI_LOTS = [
    ("bao_vat", 7, 1, 5000),    # Tiên Ngọc Tỷ (giá quầy 18.000đ)
    ("thach", "vien", 10, 500),  # 10 Viên Tinh
    ("bao_vat", 8, 1, 15000),   # Hỗn Nguyên Thạch (giá quầy 45.000đ)
]


def _events(db):
    ev = db.setdefault("events", {})
    for k in EVENT_DEFS:
        e = ev.get(k)
        if not isinstance(e, dict):
            e = {}
        e.setdefault("active", False)
        e.setdefault("ends", 0)
        ev[k] = e
    return ev


def _event_on(db, key):
    e = _events(db).get(key) or {}
    if not e.get("active"):
        return False
    ends = float(e.get("ends") or 0)
    return ends == 0 or time.time() < ends


def _events_view(db):
    now = time.time()
    out = {}
    for k, d in EVENT_DEFS.items():
        e = _events(db)[k]
        on = _event_on(db, k)
        out[k] = {"on": on, "name": d["name"], "icon": d["icon"], "desc": d["desc"],
                  "con_lai": max(0, int(float(e.get("ends") or 0) - now)) if on else 0}
    return out


def _market_fee(db):
    """Phí sàn chợ: thường 5%, mùa Đại Hội Đấu Giá chỉ 2%."""
    return 0.02 if _event_on(db, "dai_hoi") else 0.05


@app.route('/api/events', methods=['GET'])
def api_events():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    return jsonify({"success": True, "events": _events_view(db)})


@app.route('/api/admin/event', methods=['POST'])
def api_admin_event():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    data = request.json or {}
    key = data.get('key')
    action = data.get('action')
    if key not in EVENT_DEFS or action not in ("bat", "tat"):
        return jsonify({"success": False, "message": "Sự kiện hoặc hành động không hợp lệ!"})
    ev = _events(db)
    d = EVENT_DEFS[key]
    if action == "bat":
        ev[key]["active"] = True
        ev[key]["ends"] = time.time() + d["hours"] * 3600
        if key == "dai_hoi":
            m = _market(db)
            # Dọn lô Tông Môn cũ chưa ai trả giá để không bị trùng khi bật lại
            m["auctions"] = [a for a in m["auctions"]
                             if not (a.get("seller") == "tong_mon" and not a.get("bidder"))]
            for kind, kkey, qty, start in DAI_HOI_LOTS:
                m["auctions"].append({
                    "id": f"ev{int(time.time() * 1000)}-{kind}-{kkey}",
                    "seller": "tong_mon", "seller_name": "🏯 Tông Môn (Đại Hội)",
                    "kind": kind, "key": kkey, "label": _item_label(kind, kkey), "qty": qty,
                    "currency": "diem", "start": start, "cur": 0, "bidder": None, "bidder_name": "",
                    "bids": 0, "ends": ev[key]["ends"], "created": now_jst()})
        for u in db.get("users", []):
            _notify(u, f"{d['icon']} {d['name']} ĐÃ BẮT ĐẦU! {d['desc']} (Thời gian: {d['hours'] // 24} ngày{' ' if d['hours'] % 24 == 0 else ' — ' + str(d['hours'] % 24) + ' giờ'}).")
        _post_news(db, "su_kien", f"{d['icon']} SỰ KIỆN {d['name']} ĐÃ BẮT ĐẦU! {d['desc']}")
        msg = f"{d['icon']} Đã BẬT sự kiện [{d['name']}] — chạy {d['hours']} giờ kể từ bây giờ."
    else:
        ev[key]["active"] = False
        ev[key]["ends"] = 0
        if key == "dai_hoi":
            m = _market(db)
            # Lô Tông Môn chưa ai trả giá thì thu hồi; lô đã có người trả giá để kết toán bình thường.
            m["auctions"] = [a for a in m["auctions"]
                             if not (a.get("seller") == "tong_mon" and not a.get("bidder"))]
        _post_news(db, "su_kien", f"{d['icon']} Sự kiện {d['name']} đã KẾT THÚC. Cảm ơn các đệ tử đã tham gia!")
        msg = f"Đã TẮT sự kiện [{d['name']}]."
    save_database(db, f"Admin @{admin['username']} {action} su kien {key}")
    return jsonify({"success": True, "message": msg, "events": _events_view(db)})


# --- Đợt 7: Bảng Tin Tông Môn & Chat Đệ Tử (trang chủ) ---
@app.route('/api/news', methods=['GET'])
def api_news():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    return jsonify({"success": True, "news": list(reversed(db.get("news", [])[-30:]))})


@app.route('/api/admin/news', methods=['POST'])
def api_admin_news():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    text = ((request.json or {}).get('text') or '').strip()
    if not text:
        return jsonify({"success": False, "message": "Chưa nhập nội dung thông báo!"})
    text = text[:500]
    _post_news(db, "thong_bao", f"📣 THÔNG BÁO TỪ CHƯỞNG MÔN NHÂN: {text}")
    for u in db.get("users", []):
        _notify(u, f"📣 Thông báo Tông Môn mới: {text}")
    save_database(db, f"Admin @{admin['username']} dang thong bao tong mon")
    return jsonify({"success": True, "message": "Đã đăng thông báo lên Bảng Tin và gửi chuông cho toàn bộ đệ tử."})


# --- Đợt 8: Duyệt bản thảo truyện (đệ tử gửi, Tông Chủ duyệt mới lên Tàng Kinh Các) ---
@app.route('/api/submissions/mine', methods=['GET'])
def api_submissions_mine():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    mine = [{"id": s.get("id"), "time": s.get("time", ""), "title": s.get("title"),
             "status": s.get("status"), "reject_reason": s.get("reject_reason", ""),
             "source": s.get("source")}
            for s in reversed(db.get("submissions", []))
            if s.get("username") == user["username"]][:20]
    return jsonify({"success": True, "submissions": mine})


@app.route('/api/admin/submissions', methods=['GET'])
def api_admin_submissions():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    out = []
    for s in db.get("submissions", []):
        if s.get("status") != "pending":
            continue
        content = s.get("content", "")
        out.append({"id": s["id"], "time": s.get("time", ""), "username": s.get("username"),
                    "author_name": s.get("author_name"), "title": s.get("title"),
                    "category": s.get("category"), "element": s.get("element"),
                    "source": s.get("source"), "phi_nguyen": s.get("phi_nguyen", 0),
                    "so_chuong": len(_split_chapters(content)) if content.strip() else 0,
                    "do_dai": len(content), "preview": content[:1200]})
    return jsonify({"success": True, "submissions": out})


@app.route('/api/admin/submission-action', methods=['POST'])
def api_admin_submission_action():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    data = request.json or {}
    sid = data.get('id')
    action = data.get('action')
    reason = (data.get('reason') or '').strip()[:300]
    sub = next((s for s in db.get("submissions", [])
                if s.get("id") == sid and s.get("status") == "pending"), None)
    if not sub:
        return jsonify({"success": False, "message": "Không tìm thấy bản thảo đang chờ duyệt này!"})
    author = find_user(db, sub["username"])
    if action == "duyet":
        if _book_title_exists(db, sub["title"]):
            return jsonify({"success": False, "message": f"Truyện «{sub['title']}» đã có trên Tàng Kinh Các rồi — hãy Từ Chối bản thảo này."})
        new_id = max([b.get('id', 0) for b in db['books']], default=0) + 1
        cat = sub.get("category", "truyen-dich")
        db['books'].append({"id": new_id, "category": cat, "element": sub.get("element", "Kim"),
                            "author": sub.get("author_name", "Vô Danh"), "username": sub["username"],
                            "icon": "📜" if cat == "truyen-dich" else "📖",
                            "title": sub["title"], "content": sub.get("content", "")})
        sub["status"] = "approved"
        if author:
            author.setdefault('tu_luyen', get_default_tu_luyen())["diem_tich_tuyem"] += 50
            _add_diem(author, 50, f"Đăng truyện [{sub['title']}] lên Tàng Kinh Các (Tông Chủ duyệt)")
            _add_exp(author, 50)
            _bump(author, "dang_truyen")
            _notify(author, f"✅ Bản thảo truyện «{sub['title']}» ĐÃ ĐƯỢC Tông Chủ DUYỆT — truyện đã lên Tàng Kinh Các! +50 Điểm Cống Hiến.")
            if sub.get("source") == "bag" and sub.get("bag_item_id"):
                author["bag"] = [i for i in author.get("bag", []) if i.get("item_id") != sub["bag_item_id"]]
        _post_news(db, "truyen", f"📚 {sub.get('author_name', '')} vừa cống hiến truyện mới «{sub['title']}» lên Tàng Kinh Các!")
        save_database(db, f"Admin duyet ban thao #{sid}: {sub['title']}")
        return jsonify({"success": True, "message": f"✅ Đã duyệt! Truyện «{sub['title']}» đã lên Tàng Kinh Các."})
    if action == "tu_choi":
        sub["status"] = "rejected"
        sub["reject_reason"] = reason
        if author:
            _notify(author, f"❌ Bản thảo truyện «{sub['title']}» bị Tông Chủ TỪ CHỐI." + (f" Lý do: {reason}" if reason else ""))
            if sub.get("source") == "bag" and int(sub.get("phi_nguyen", 0)) > 0:
                _add_stone(author, "nguyen", int(sub["phi_nguyen"]))
                _notify(author, f"💰 Đã hoàn lại {sub['phi_nguyen']} Nguyên Tinh phí chuyển truyện «{sub['title']}». Truyện vẫn nằm trong Túi Trữ Vật của đạo hữu.")
        save_database(db, f"Admin tu choi ban thao #{sid}: {sub['title']}")
        return jsonify({"success": True, "message": f"Đã từ chối bản thảo «{sub['title']}»."})
    return jsonify({"success": False, "message": "Hành động không hợp lệ!"})


# --- Đợt 9: Ví tiền yên & nạp tiền (Tông Chủ duyệt tay), đổi Điểm Cống Hiến ---
# Quy định của ông chủ: 100 yên = 1.000 Điểm Cống Hiến (1 yên = 10 điểm).
# Tiền nạp nằm trong Ví trên app; Ví Tông Môn đủ 30 man (300.000 yên) thì
# Tông Chủ chuyển về tài khoản ngân hàng 1 lần (ghi nhận tại trang Quản Trị).
NAP_RATE = 10
NAP_MIN = 100
PAYOUT_TARGET = 300000


def _fmt_yen(n):
    return f"{int(n):,}".replace(",", ".") + "¥"


def _bank_info(db):
    return (db.get("settings") or {}).get("bank_info", "")


@app.route('/api/wallet', methods=['GET'])
def api_wallet():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    g = _ensure_game(user)
    mine = [{"id": t["id"], "time": t.get("time", ""), "yen": t.get("yen"), "code": t.get("code"),
             "status": t.get("status"), "reject_reason": t.get("reject_reason", "")}
            for t in reversed(db.get("topups", [])) if t.get("username") == user["username"]][:15]
    return jsonify({"success": True, "vi_yen": int(g.get("vi_yen", 0)), "diem": int(g.get("diem", 0)),
                    "rate": NAP_RATE, "min": NAP_MIN, "bank_info": _bank_info(db), "topups": mine})


@app.route('/api/wallet/topup', methods=['POST'])
def api_wallet_topup():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    try:
        yen = int((request.json or {}).get('yen') or 0)
    except Exception:
        yen = 0
    if yen < NAP_MIN or yen % 100 != 0 or yen > 1000000:
        return jsonify({"success": False, "message": f"Số yên nạp phải từ {_fmt_yen(NAP_MIN)} trở lên và là bội số của 100¥ (vd 100, 500, 1.000, 10.000)."})
    tops = db.setdefault("topups", [])
    tid = (int(tops[-1].get("id", 0)) + 1) if tops else 1
    code = f"NAP{tid}"
    tops.append({"id": tid, "ts": time.time(), "time": now_jst(), "username": user["username"],
                 "author_name": user.get("author_name", user["username"]), "yen": yen, "code": code,
                 "status": "cho_chuyen", "reject_reason": ""})
    db["topups"] = tops[-300:]
    _notify(user, f"💳 Đã tạo lệnh nạp {_fmt_yen(yen)} (mã {code}). Hãy chuyển khoản đúng số tiền với nội dung chuyển khoản là «{code}», rồi quay lại trang Ví bấm «Đã Chuyển Khoản». Tông Chủ duyệt xong, tiền sẽ vào Ví của đạo hữu.")
    _notify_admins(db, f"💳 Lệnh nạp tiền mới: {_fmt_yen(yen)} của {user.get('author_name', user['username'])} (mã {code}) — chờ đệ tử chuyển khoản & Tông Chủ duyệt.")
    save_database(db, f"@{user['username']} tao lenh nap {yen} yen ({code})")
    return jsonify({"success": True, "id": tid, "code": code,
                    "message": f"💳 Đã tạo lệnh nạp {_fmt_yen(yen)} — mã chuyển khoản: {code}. Hãy chuyển khoản đúng nội dung «{code}», xong quay lại bấm «Đã Chuyển Khoản» nhé!"})


@app.route('/api/wallet/topup-done', methods=['POST'])
def api_wallet_topup_done():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    tid = (request.json or {}).get('id')
    t = next((x for x in db.get("topups", [])
              if x.get("id") == tid and x.get("username") == user["username"]), None)
    if not t or t.get("status") != "cho_chuyen":
        return jsonify({"success": False, "message": "Không tìm thấy lệnh nạp đang chờ của đạo hữu!"})
    t["status"] = "da_chuyen"
    _notify_admins(db, f"💳 {user.get('author_name', user['username'])} báo ĐÃ CHUYỂN KHOẢN lệnh nạp {_fmt_yen(t['yen'])} (mã {t['code']}) — chờ Tông Chủ đối chiếu & duyệt.")
    save_database(db, f"@{user['username']} bao da chuyen khoan lenh nap #{tid}")
    return jsonify({"success": True, "message": "Đã ghi nhận! Tông Chủ sẽ đối chiếu sao kê rồi duyệt — tiền vào Ví sẽ có chuông báo cho đạo hữu."})


@app.route('/api/wallet/doi-diem', methods=['POST'])
def api_wallet_doi_diem():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    try:
        yen = int((request.json or {}).get('yen') or 0)
    except Exception:
        yen = 0
    g = _ensure_game(user)
    if yen < NAP_MIN or yen % 100 != 0:
        return jsonify({"success": False, "message": "Mỗi lần đổi tối thiểu 100¥ và phải là bội số của 100¥."})
    if int(g.get("vi_yen", 0)) < yen:
        return jsonify({"success": False, "message": f"Ví không đủ: đang có {_fmt_yen(g.get('vi_yen', 0))}, muốn đổi {_fmt_yen(yen)}."})
    g["vi_yen"] = int(g.get("vi_yen", 0)) - yen
    diem_nhan = yen * NAP_RATE
    _add_diem(user, diem_nhan, f"Đổi {_fmt_yen(yen)} từ Ví sang Điểm Cống Hiến")
    _log(user, f"Đổi {_fmt_yen(yen)} → +{diem_nhan:,} Điểm Cống Hiến".replace(",", "."))
    save_database(db, f"@{user['username']} doi {yen} yen sang {diem_nhan} diem")
    return jsonify({"success": True, "vi_yen": g["vi_yen"], "diem": g["diem"],
                    "message": f"✅ Đã đổi {_fmt_yen(yen)} → +{diem_nhan:,} Điểm Cống Hiến!".replace(",", ".")})


@app.route('/api/admin/topups', methods=['GET'])
def api_admin_topups():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    out = [{"id": t["id"], "time": t.get("time", ""), "username": t.get("username"),
            "author_name": t.get("author_name"), "yen": t.get("yen"), "code": t.get("code"),
            "status": t.get("status")}
           for t in reversed(db.get("topups", [])) if t.get("status") in ("cho_chuyen", "da_chuyen")][:50]
    tong_nap = sum(int(t.get("yen", 0)) for t in db.get("topups", []) if t.get("status") == "approved")
    da_rut = sum(int(p.get("yen", 0)) for p in db.get("payouts", []))
    return jsonify({"success": True, "topups": out, "bank_info": _bank_info(db),
                    "vi_tong_mon": {"tong_nap": tong_nap, "da_rut": da_rut,
                                    "dang_giu": tong_nap - da_rut, "muc_tieu": PAYOUT_TARGET,
                                    "payouts": list(reversed(db.get("payouts", [])))[:10]}})


@app.route('/api/admin/topup-action', methods=['POST'])
def api_admin_topup_action():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    data = request.json or {}
    tid, action = data.get('id'), data.get('action')
    reason = (data.get('reason') or '').strip()[:300]
    t = next((x for x in db.get("topups", [])
              if x.get("id") == tid and x.get("status") in ("cho_chuyen", "da_chuyen")), None)
    if not t:
        return jsonify({"success": False, "message": "Không tìm thấy lệnh nạp đang chờ này!"})
    target = find_user(db, t["username"])
    if action == "duyet":
        t["status"] = "approved"
        if target:
            g = _ensure_game(target)
            g["vi_yen"] = int(g.get("vi_yen", 0)) + int(t["yen"])
            _notify(target, f"💰 Lệnh nạp {_fmt_yen(t['yen'])} (mã {t['code']}) ĐÃ ĐƯỢC DUYỆT — tiền đã vào Ví của đạo hữu! Vào trang Ví để đổi sang Điểm Cống Hiến khi cần.")
        _post_news(db, "nap_tien", f"💳 {t.get('author_name', '')} vừa nạp {_fmt_yen(t['yen'])} vào Ví Tông Môn. Cảm ơn đạo hữu đã ủng hộ Tông Môn! 🙏")
        save_database(db, f"Admin duyet lenh nap #{tid}: {t['yen']} yen cua @{t['username']}")
        return jsonify({"success": True, "message": f"✅ Đã duyệt lệnh nạp {_fmt_yen(t['yen'])} — tiền đã vào Ví của {t.get('author_name', '')}."})
    if action == "tu_choi":
        t["status"] = "rejected"
        t["reject_reason"] = reason
        if target:
            _notify(target, f"❌ Lệnh nạp {_fmt_yen(t['yen'])} (mã {t['code']}) bị TỪ CHỐI." + (f" Lý do: {reason}" if reason else ""))
        save_database(db, f"Admin tu choi lenh nap #{tid}")
        return jsonify({"success": True, "message": "Đã từ chối lệnh nạp."})
    return jsonify({"success": False, "message": "Hành động không hợp lệ!"})


@app.route('/api/admin/payout', methods=['POST'])
def api_admin_payout():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    tong_nap = sum(int(t.get("yen", 0)) for t in db.get("topups", []) if t.get("status") == "approved")
    da_rut = sum(int(p.get("yen", 0)) for p in db.get("payouts", []))
    dang_giu = tong_nap - da_rut
    try:
        yen = int((request.json or {}).get('yen') or dang_giu)
    except Exception:
        yen = dang_giu
    if yen <= 0 or yen > dang_giu:
        return jsonify({"success": False, "message": f"Số tiền rút không hợp lệ — Ví Tông Môn đang giữ {_fmt_yen(dang_giu)}."})
    pays = db.setdefault("payouts", [])
    pid = (int(pays[-1].get("id", 0)) + 1) if pays else 1
    pays.append({"id": pid, "ts": time.time(), "time": now_jst(), "yen": yen, "by": admin["username"]})
    save_database(db, f"Admin ghi rut {yen} yen ve tai khoan (lan #{pid})")
    return jsonify({"success": True, "message": f"🏦 Đã ghi nhận chuyển {_fmt_yen(yen)} về tài khoản. Ví Tông Môn còn giữ {_fmt_yen(dang_giu - yen)}."})


@app.route('/api/admin/bank-info', methods=['POST'])
def api_admin_bank_info():
    db = load_database()
    admin, err = _admin_or_403(db)
    if err:
        return err
    text = ((request.json or {}).get('text') or '').strip()[:1000]
    db.setdefault("settings", {})["bank_info"] = text
    save_database(db, "Admin cap nhat thong tin nhan tien nap")
    return jsonify({"success": True, "message": "Đã lưu thông tin nhận tiền — đệ tử sẽ thấy ở trang Ví Nạp Tiền."})


@app.route('/api/chat', methods=['GET'])
def api_chat_get():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    return jsonify({"success": True, "messages": db.get("chat", [])[-60:]})


@app.route('/api/chat', methods=['POST'])
def api_chat_post():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    text = ((request.json or {}).get('text') or '').strip()
    if not text:
        return jsonify({"success": False, "message": "Chưa nhập tin nhắn!"})
    if len(text) > 300:
        return jsonify({"success": False, "message": "Tin nhắn tối đa 300 ký tự!"})
    chat = db.setdefault("chat", [])
    now = time.time()
    last = next((m for m in reversed(chat) if m.get("username") == user["username"]), None)
    if last and now - float(last.get("ts", 0)) < 3:
        return jsonify({"success": False, "message": "Đạo hữu nhắn nhanh quá — đợi 3 giây rồi nhắn tiếp nhé!"})
    g = _ensure_game(user)
    chat.append({
        "id": (int(chat[-1].get("id", 0)) + 1) if chat else 1,
        "ts": now, "time": now_jst(),
        "username": user["username"],
        "author_name": user.get("author_name", user["username"]),
        "role": user.get("role", "member"),
        "chibi": g.get("chibi"),
        "text": text,
    })
    db["chat"] = chat[-200:]
    save_database(db, f"@{user['username']} chat tong mon")
    return jsonify({"success": True, "messages": db["chat"][-60:]})


@app.route('/api/leaderboard', methods=['GET'])
def api_leaderboard():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    rows = []
    for u in db.get("users", []):
        if (u.get("game") or {}).get("locked"):
            continue  # Tài khoản đã khóa không lên Thiên Thư Bảng
        g = _ensure_game(u)
        tv = g["tu_vi"]
        muc, cap = int(tv.get("muc", 1)), int(tv.get("cap", -1))
        rows.append({
            "name": u.get("author_name", u["username"]),
            "chibi": g.get("chibi"),
            "tv_label": f"{REALM_NAMES[muc - 1]} · {STAGE_NAMES[cap] if cap >= 0 else 'Chưa Nhập Môn'}",
            "tv_key": (muc, cap, int(g.get("linh_khi", 0))),
            "diem": int(g.get("diem", 0)),
            "dang": int(g["stats"].get("total", {}).get("dang_truyen", 0)),
            "exp": int(g.get("exp", 0)),
        })
    def fmt(n):
        return f"{n:,}".replace(",", ".")
    top = lambda key, label: [{"name": r["name"], "label": label(r), "chibi": r.get("chibi")} for r in sorted(rows, key=key, reverse=True)[:5]]
    boards = {
        "tu_vi": top(lambda r: r["tv_key"], lambda r: r["tv_label"]),
        "diem": top(lambda r: r["diem"], lambda r: f"{fmt(r['diem'])} điểm"),
        "dang": top(lambda r: r["dang"], lambda r: f"{r['dang']} truyện đã đăng"),
        "hoat_dong": top(lambda r: r["exp"], lambda r: f"Cấp {1 + r['exp'] // 250} · {fmt(r['exp'])} EXP"),
    }
    return jsonify({"success": True, "boards": boards})


# --- Chợ Đấu Giá (Đợt 3): buôn bán / đấu giá / trao đổi ---
FISH_NAMES = {"thuong": "Cá Thường", "ngon": "Cá Ngon", "linh_ngu": "Linh Ngư"}
PILL_NAMES = {"hoi_linh": "Hồi Linh Đan", "tu_linh": "Tụ Linh Đan"}


def _market(db):
    m = db.setdefault("market", {"seq": 1, "listings": [], "auctions": [], "barters": [], "deals": []})
    for k, v in {"seq": 1, "listings": [], "auctions": [], "barters": [], "deals": []}.items():
        if k not in m:
            m[k] = v
    return m


def _item_valid(kind, key):
    try:
        if kind == "thach":
            return key in STONE_TYPES
        if kind in ("thao", "quang", "phap_khi"):
            return 1 <= int(key) <= 9
        if kind == "dan":
            return key in PILL_NAMES
        if kind == "ca":
            return key in FISH_NAMES
        if kind == "bao_vat":
            return any(t["id"] == key for t in TREASURES)
    except Exception:
        return False
    return False


def _item_label(kind, key):
    if kind == "thach":
        return STONE_TYPES[key]["name"]
    if kind == "thao":
        return f"{HERBS[int(key) - 1]['name']} (Thảo Cấp {key})"
    if kind == "quang":
        return f"Quặng Cấp {key}"
    if kind == "phap_khi":
        return f"Pháp Khí Cấp {key}"
    if kind == "dan":
        return PILL_NAMES[key]
    if kind == "ca":
        return FISH_NAMES[key]
    if kind == "bao_vat":
        t = next(x for x in TREASURES if x["id"] == key)
        return f"{t['name']} (Bảo Vật)"
    return str(key)


def _item_count(user, kind, key):
    g = _ensure_game(user)
    if kind == "thach":
        return int(g["linh_thach"].get(key, 0))
    if kind == "thao":
        return int(g["inventory"].get("herbs", {}).get(str(key), 0))
    if kind == "quang":
        return int(_nghe(user)["quang"].get(str(key), 0))
    if kind == "dan":
        return int(_nghe(user)["dan"].get(key, 0))
    if kind == "ca":
        return int(_nghe(user)["ca"].get(key, 0))
    if kind == "phap_khi":
        return _nghe(user)["phap_khi"].count(int(key))
    if kind == "bao_vat":
        return 1 if key in g["inventory"].get("treasures", []) else 0
    return 0


def _item_add(user, kind, key, qty):
    g = _ensure_game(user)
    ng = _nghe(user)
    if kind == "thach":
        _add_stone(user, key, qty)
    elif kind == "thao":
        h = g["inventory"].setdefault("herbs", {})
        h[str(key)] = int(h.get(str(key), 0)) + qty
    elif kind == "quang":
        ng["quang"][str(key)] = int(ng["quang"].get(str(key), 0)) + qty
    elif kind == "dan":
        ng["dan"][key] = int(ng["dan"].get(key, 0)) + qty
    elif kind == "ca":
        ng["ca"][key] = int(ng["ca"].get(key, 0)) + qty
    elif kind == "phap_khi":
        ng["phap_khi"].extend([int(key)] * qty)
    elif kind == "bao_vat":
        if key not in g["inventory"].setdefault("treasures", []):
            g["inventory"]["treasures"].append(key)


def _item_remove(user, kind, key, qty):
    if _item_count(user, kind, key) < qty:
        return False
    g = _ensure_game(user)
    ng = _nghe(user)
    if kind == "thach":
        _add_stone(user, key, -qty)
    elif kind == "thao":
        g["inventory"]["herbs"][str(key)] -= qty
    elif kind == "quang":
        ng["quang"][str(key)] -= qty
    elif kind == "dan":
        ng["dan"][key] -= qty
    elif kind == "ca":
        ng["ca"][key] -= qty
    elif kind == "phap_khi":
        for _ in range(qty):
            ng["phap_khi"].remove(int(key))
    elif kind == "bao_vat":
        g["inventory"]["treasures"].remove(key)
    return True


def _my_items(user):
    out = []
    for loai in STONE_TYPES:
        n = _item_count(user, "thach", loai)
        if n > 0:
            out.append({"kind": "thach", "key": loai, "label": _item_label("thach", loai), "qty": n})
    for kind in ("thao", "quang", "phap_khi"):
        for t in range(1, 10):
            n = _item_count(user, kind, t)
            if n > 0:
                out.append({"kind": kind, "key": str(t), "label": _item_label(kind, t), "qty": n})
    for kind, names in (("dan", PILL_NAMES), ("ca", FISH_NAMES)):
        for key in names:
            n = _item_count(user, kind, key)
            if n > 0:
                out.append({"kind": kind, "key": key, "label": _item_label(kind, key), "qty": n})
    for t in TREASURES:
        if _item_count(user, "bao_vat", t["id"]) > 0:
            out.append({"kind": "bao_vat", "key": t["id"], "label": _item_label("bao_vat", t["id"]), "qty": 1})
    return out


def _item_catalog():
    out = []
    for loai in STONE_TYPES:
        out.append({"kind": "thach", "key": loai, "label": _item_label("thach", loai)})
    for kind in ("thao", "quang", "phap_khi"):
        for t in range(1, 10):
            out.append({"kind": kind, "key": str(t), "label": _item_label(kind, t)})
    for kind, names in (("dan", PILL_NAMES), ("ca", FISH_NAMES)):
        for key in names:
            out.append({"kind": kind, "key": key, "label": _item_label(kind, key)})
    for t in TREASURES:
        out.append({"kind": "bao_vat", "key": t["id"], "label": _item_label("bao_vat", t["id"])})
    return out


def _can_sell(user):
    # Đợt 10 (theo ý ông chủ): MỌI đệ tử đều được rao bán / mở đấu giá / trao đổi
    # ở Chợ Đấu Giá để giao thương và kiếm Điểm Cống Hiến — bỏ cổng Cấp Hoạt Động 5.
    return True


def _cur_name(currency):
    return "Điểm Cống Hiến" if currency == "diem" else "Nguyên Tinh"


def _min_step(currency, cur):
    """Bước giá tối thiểu: điểm thì 10, Nguyên Tinh thì 1 (hoặc 5% giá hiện tại)."""
    base = 10 if currency == "diem" else 1
    return max(base, -(-int(cur) * 5 // 100))


def _cur_balance(user, currency):
    g = _ensure_game(user)
    return int(g.get("diem", 0)) if currency == "diem" else int(g["linh_thach"].get("nguyen", 0))


def _cur_pay(user, currency, amount):
    if _cur_balance(user, currency) < amount:
        return False
    if currency == "diem":
        _add_diem(user, -amount)
    else:
        _add_stone(user, "nguyen", -amount)
    return True


def _cur_grant(user, currency, amount):
    if currency == "diem":
        _add_diem(user, amount)
    else:
        _add_stone(user, "nguyen", amount)


def _deal(db, text):
    m = _market(db)
    m["deals"].append({"time": now_jst(), "text": text})
    m["deals"] = m["deals"][-30:]


def _settle_auctions(db):
    m = _market(db)
    now = time.time()
    changed = False
    for a in list(m["auctions"]):
        if a["ends"] <= now:
            seller = find_user(db, a["seller"])
            if a.get("bidder"):
                winner = find_user(db, a["bidder"])
                fee = _market_fee(db)
                pay = int(a["cur"] * (1 - fee))
                if seller:
                    _cur_grant(seller, a["currency"], pay)
                    _notify(seller, f"🔨 Đấu giá [{a['label']} ×{a['qty']}] đã kết thúc: {a['bidder_name']} thắng với {a['cur']} {_cur_name(a['currency'])} — đạo hữu nhận {pay} (sau phí sàn {int(fee * 100)}%).")
                if winner:
                    _item_add(winner, a["kind"], a["key"], a["qty"])
                    _notify(winner, f"🏆 Đạo hữu đã THẮNG đấu giá [{a['label']} ×{a['qty']}] với giá {a['cur']} {_cur_name(a['currency'])}! Vật phẩm đã về kho.")
                    _log(winner, f"Thắng đấu giá {a['label']} ×{a['qty']} ({a['cur']} {_cur_name(a['currency'])})")
                _deal(db, f"🔨 {a['bidder_name']} thắng đấu giá {a['label']} ×{a['qty']} với {a['cur']} {_cur_name(a['currency'])} (người bán: {a['seller_name']}).")
            else:
                if seller:
                    _item_add(seller, a["kind"], a["key"], a["qty"])
                    _notify(seller, f"🔨 Đấu giá [{a['label']} ×{a['qty']}] hết giờ mà không có ai trả giá — vật phẩm đã trả lại kho.")
                _deal(db, f"🔨 Đấu giá {a['label']} ×{a['qty']} của {a['seller_name']} hết giờ, không có người trả giá.")
            m["auctions"].remove(a)
            changed = True
    return changed


def _market_view(db, user):
    m = _market(db)
    now = time.time()
    me = user["username"]
    listings = [dict(l, mine=(l["seller"] == me)) for l in m["listings"]]
    auctions = []
    for a in m["auctions"]:
        aa = dict(a)
        aa["mine"] = a["seller"] == me
        aa["dang_thang"] = a.get("bidder") == me
        aa["con_lai"] = max(0, int(a["ends"] - now))
        aa["gia_toi_thieu"] = a["start"] if not a.get("bidder") else a["cur"] + _min_step(a["currency"], a["cur"])
        auctions.append(aa)
    barters = [dict(b, mine=(b["seller"] == me)) for b in m["barters"]]
    g = _ensure_game(user)
    return {
        "listings": listings, "auctions": auctions, "barters": barters,
        "deals": list(reversed(m["deals"][-12:])),
        "my_items": _my_items(user), "catalog": _item_catalog(),
        "can_sell": _can_sell(user), "cap_hoat_dong": 1 + int(g.get("exp", 0)) // 250,
        "phi_san": int(_market_fee(db) * 100), "su_kien": _events_view(db),
    }


def _cho_resp(db, user, message):
    return jsonify({"success": True, "message": message, "game": game_summary(user), "cho": _market_view(db, user)})


@app.route('/api/cho', methods=['GET'])
def api_cho():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if _settle_auctions(db):
        save_database(db, "ket thuc dau gia het gio")
    return jsonify({"success": True, "game": game_summary(user), "cho": _market_view(db, user)})


@app.route('/api/cho/rao-ban', methods=['POST'])
def api_cho_rao_ban():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if not _can_sell(user):
        return jsonify({"success": False, "message": "Phải đạt Cấp Hoạt Động 5 mới được rao bán ở Chợ Đấu Giá (chống tài khoản rác). Hãy đọc truyện, làm nhiệm vụ để lên cấp nhé!"})
    data = request.json or {}
    kind, key = data.get('kind'), str(data.get('key'))
    qty, price = int(data.get('qty', 0) or 0), int(data.get('price', 0) or 0)
    if not _item_valid(kind, key):
        return jsonify({"success": False, "message": "Vật phẩm không hợp lệ!"})
    if qty < 1 or price < 1:
        return jsonify({"success": False, "message": "Số lượng và giá bán phải từ 1 trở lên!"})
    m = _market(db)
    cap_rao = 10 if _event_on(db, "dai_hoi") else 5
    if sum(1 for l in m["listings"] if l["seller"] == user["username"]) >= cap_rao:
        return jsonify({"success": False, "message": f"Mỗi người chỉ được rao tối đa {cap_rao} món cùng lúc!"})
    if not _item_remove(user, kind, key, qty):
        return jsonify({"success": False, "message": "Không đủ vật phẩm trong kho để rao bán!"})
    lid = f"l{int(time.time()*1000)}-{user['username']}"
    m["listings"].append({"id": lid, "seller": user["username"], "seller_name": user.get("author_name", user["username"]),
                          "kind": kind, "key": key, "label": _item_label(kind, key), "qty": qty, "price": price,
                          "created": now_jst()})
    _log(user, f"Rao bán {_item_label(kind, key)} ×{qty} với giá {price} điểm")
    save_database(db, f"@{user['username']} rao ban {kind}:{key} x{qty} gia {price}")
    return _cho_resp(db, user, f"🏷️ Đã rao bán {_item_label(kind, key)} ×{qty} với giá {price} Điểm Cống Hiến. Vật phẩm đang ký quỹ ở chợ, hủy rao sẽ trả lại.")


@app.route('/api/cho/huy-rao', methods=['POST'])
def api_cho_huy_rao():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _market(db)
    lid = (request.json or {}).get('id')
    l = next((x for x in m["listings"] if x["id"] == lid), None)
    if not l or l["seller"] != user["username"]:
        return jsonify({"success": False, "message": "Không tìm thấy tin rao của đạo hữu!"})
    _item_add(user, l["kind"], l["key"], l["qty"])
    m["listings"].remove(l)
    save_database(db, f"@{user['username']} huy rao ban {lid}")
    return _cho_resp(db, user, f"Đã hủy rao và trả lại {l['label']} ×{l['qty']} vào kho.")


@app.route('/api/cho/mua', methods=['POST'])
def api_cho_mua():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _market(db)
    lid = (request.json or {}).get('id')
    l = next((x for x in m["listings"] if x["id"] == lid), None)
    if not l:
        return jsonify({"success": False, "message": "Tin rao này không còn nữa!"})
    if l["seller"] == user["username"]:
        return jsonify({"success": False, "message": "Không thể mua đồ của chính mình!"})
    g = _ensure_game(user)
    if int(g.get("diem", 0)) < l["price"]:
        return jsonify({"success": False, "message": f"Không đủ Điểm Cống Hiến (cần {l['price']}, đang có {g.get('diem', 0)})!"})
    seller = find_user(db, l["seller"])
    _add_diem(user, -l["price"], f"Mua {l['label']} ở Chợ Đấu Giá")
    _item_add(user, l["kind"], l["key"], l["qty"])
    fee = _market_fee(db)
    nhan = int(l["price"] * (1 - fee))
    if seller:
        _add_diem(seller, nhan, f"Bán {l['label']} ở Chợ Đấu Giá (sau phí {int(fee * 100)}%)")
        _notify(seller, f"🏷️ {user.get('author_name', user['username'])} đã mua [{l['label']} ×{l['qty']}] của đạo hữu ở Chợ Đấu Giá: +{nhan} Điểm Cống Hiến (giá {l['price']}, trừ phí sàn {int(fee * 100)}%).")
        _log(seller, f"Bán {l['label']} ×{l['qty']} ở chợ (+{nhan} điểm)")
    _deal(db, f"🏷️ {user.get('author_name', user['username'])} mua {l['label']} ×{l['qty']} của {l['seller_name']} với {l['price']} điểm.")
    m["listings"].remove(l)
    save_database(db, f"@{user['username']} mua {l['kind']}:{l['key']} gia {l['price']}")
    return _cho_resp(db, user, f"🛍️ Đã mua {l['label']} ×{l['qty']} với {l['price']} Điểm Cống Hiến. Vật phẩm đã về kho của đạo hữu!")


@app.route('/api/cho/dau-gia/tao', methods=['POST'])
def api_cho_dau_gia_tao():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if not _can_sell(user):
        return jsonify({"success": False, "message": "Phải đạt Cấp Hoạt Động 5 mới được mở đấu giá ở Chợ Đấu Giá (chống tài khoản rác)!"})
    data = request.json or {}
    kind, key = data.get('kind'), str(data.get('key'))
    qty, start = int(data.get('qty', 0) or 0), int(data.get('start', 0) or 0)
    currency = data.get('currency') if data.get('currency') in ("diem", "nguyen") else "diem"
    hours = int(data.get('hours', 12) or 12)
    if hours not in (6, 12, 24):
        hours = 12
    if not _item_valid(kind, key):
        return jsonify({"success": False, "message": "Vật phẩm không hợp lệ!"})
    if qty < 1 or start < 1:
        return jsonify({"success": False, "message": "Số lượng và giá khởi điểm phải từ 1 trở lên!"})
    m = _market(db)
    cap_phien = 6 if _event_on(db, "dai_hoi") else 3
    if sum(1 for a in m["auctions"] if a["seller"] == user["username"]) >= cap_phien:
        return jsonify({"success": False, "message": f"Mỗi người chỉ được mở tối đa {cap_phien} phiên đấu giá cùng lúc!"})
    if not _item_remove(user, kind, key, qty):
        return jsonify({"success": False, "message": "Không đủ vật phẩm trong kho để đấu giá!"})
    aid = f"a{int(time.time()*1000)}-{user['username']}"
    m["auctions"].append({"id": aid, "seller": user["username"], "seller_name": user.get("author_name", user["username"]),
                          "kind": kind, "key": key, "label": _item_label(kind, key), "qty": qty,
                          "currency": currency, "start": start, "cur": 0, "bidder": None, "bidder_name": "",
                          "bids": 0, "ends": time.time() + hours * 3600, "created": now_jst()})
    _log(user, f"Mở đấu giá {_item_label(kind, key)} ×{qty}, khởi điểm {start} {_cur_name(currency)}, {hours} giờ")
    save_database(db, f"@{user['username']} mo dau gia {kind}:{key} x{qty}")
    return _cho_resp(db, user, f"🔨 Đã mở phiên đấu giá {_item_label(kind, key)} ×{qty}: khởi điểm {start} {_cur_name(currency)}, kéo dài {hours} giờ.")


@app.route('/api/cho/dau-gia/dat', methods=['POST'])
def api_cho_dau_gia_dat():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    _settle_auctions(db)
    m = _market(db)
    data = request.json or {}
    a = next((x for x in m["auctions"] if x["id"] == data.get('id')), None)
    if not a:
        return jsonify({"success": False, "message": "Phiên đấu giá này đã kết thúc hoặc không tồn tại!"})
    if a["seller"] == user["username"]:
        return jsonify({"success": False, "message": "Không thể tự trả giá đồ của mình!"})
    if a.get("bidder") == user["username"]:
        return jsonify({"success": False, "message": "Đạo hữu đang là người trả giá cao nhất rồi!"})
    amount = int(data.get('amount', 0) or 0)
    toi_thieu = a["start"] if not a.get("bidder") else a["cur"] + _min_step(a["currency"], a["cur"])
    if amount < toi_thieu:
        return jsonify({"success": False, "message": f"Giá trả tối thiểu lúc này là {toi_thieu} {_cur_name(a['currency'])}!"})
    if not _cur_pay(user, a["currency"], amount):
        return jsonify({"success": False, "message": f"Không đủ {_cur_name(a['currency'])} để đặt giá {amount} (tiền sẽ ký quỹ, thua sẽ hoàn)!"})
    if a.get("bidder"):
        prev = find_user(db, a["bidder"])
        if prev:
            _cur_grant(prev, a["currency"], a["cur"])
            _notify(prev, f"🔨 Đạo hữu vừa bị TRẢ GIÁ CAO HƠN ở phiên [{a['label']} ×{a['qty']}]: giá mới {amount} {_cur_name(a['currency'])}. Đã hoàn lại {a['cur']} {_cur_name(a['currency'])} ký quỹ.")
    a["cur"], a["bidder"], a["bidder_name"] = amount, user["username"], user.get("author_name", user["username"])
    a["bids"] = int(a.get("bids", 0)) + 1
    keo_dai = ""
    if a["ends"] - time.time() < 120:
        a["ends"] = time.time() + 120
        keo_dai = " (trả giá phút cuối: phiên tự kéo dài thêm 2 phút)"
    _log(user, f"Trả giá {amount} {_cur_name(a['currency'])} cho {a['label']}")
    save_database(db, f"@{user['username']} tra gia {amount} phien {a['id']}")
    return _cho_resp(db, user, f"🔨 Đã đặt giá {amount} {_cur_name(a['currency'])} cho [{a['label']} ×{a['qty']}] — đạo hữu đang dẫn đầu!{keo_dai} Tiền đã ký quỹ, nếu thua sẽ hoàn 100%.")


@app.route('/api/cho/dau-gia/huy', methods=['POST'])
def api_cho_dau_gia_huy():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _market(db)
    a = next((x for x in m["auctions"] if x["id"] == (request.json or {}).get('id')), None)
    if not a or a["seller"] != user["username"]:
        return jsonify({"success": False, "message": "Không tìm thấy phiên đấu giá của đạo hữu!"})
    if a.get("bidder"):
        return jsonify({"success": False, "message": "Đã có người trả giá, không hủy được nữa!"})
    _item_add(user, a["kind"], a["key"], a["qty"])
    m["auctions"].remove(a)
    save_database(db, f"@{user['username']} huy dau gia {a['id']}")
    return _cho_resp(db, user, f"Đã hủy phiên đấu giá và trả lại {a['label']} ×{a['qty']} vào kho.")


@app.route('/api/cho/trao-doi/tao', methods=['POST'])
def api_cho_trao_doi_tao():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    if not _can_sell(user):
        return jsonify({"success": False, "message": "Phải đạt Cấp Hoạt Động 5 mới được rao trao đổi ở Chợ Đấu Giá (chống tài khoản rác)!"})
    data = request.json or {}
    gk, gkey, gqty = data.get('give_kind'), str(data.get('give_key')), int(data.get('give_qty', 0) or 0)
    wk, wkey, wqty = data.get('want_kind'), str(data.get('want_key')), int(data.get('want_qty', 0) or 0)
    if not _item_valid(gk, gkey) or not _item_valid(wk, wkey):
        return jsonify({"success": False, "message": "Vật phẩm đổi/muốn không hợp lệ!"})
    if gqty < 1 or wqty < 1:
        return jsonify({"success": False, "message": "Số lượng phải từ 1 trở lên!"})
    m = _market(db)
    if sum(1 for b in m["barters"] if b["seller"] == user["username"]) >= 3:
        return jsonify({"success": False, "message": "Mỗi người chỉ được rao tối đa 3 tin trao đổi cùng lúc!"})
    if not _item_remove(user, gk, gkey, gqty):
        return jsonify({"success": False, "message": "Không đủ vật phẩm đem đổi trong kho!"})
    bid = f"b{int(time.time()*1000)}-{user['username']}"
    m["barters"].append({"id": bid, "seller": user["username"], "seller_name": user.get("author_name", user["username"]),
                         "give": {"kind": gk, "key": gkey, "label": _item_label(gk, gkey), "qty": gqty},
                         "want": {"kind": wk, "key": wkey, "label": _item_label(wk, wkey), "qty": wqty},
                         "created": now_jst()})
    save_database(db, f"@{user['username']} rao trao doi {gk}:{gkey} lay {wk}:{wkey}")
    return _cho_resp(db, user, f"🔁 Đã rao đổi: {_item_label(gk, gkey)} ×{gqty} lấy {_item_label(wk, wkey)} ×{wqty}. Vật phẩm đã ký quỹ ở chợ.")


@app.route('/api/cho/trao-doi/huy', methods=['POST'])
def api_cho_trao_doi_huy():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _market(db)
    b = next((x for x in m["barters"] if x["id"] == (request.json or {}).get('id')), None)
    if not b or b["seller"] != user["username"]:
        return jsonify({"success": False, "message": "Không tìm thấy tin trao đổi của đạo hữu!"})
    _item_add(user, b["give"]["kind"], b["give"]["key"], b["give"]["qty"])
    m["barters"].remove(b)
    save_database(db, f"@{user['username']} huy trao doi {b['id']}")
    return _cho_resp(db, user, f"Đã hủy tin trao đổi và trả lại {b['give']['label']} ×{b['give']['qty']} vào kho.")


@app.route('/api/cho/trao-doi/chap-nhan', methods=['POST'])
def api_cho_trao_doi_chap_nhan():
    db = load_database()
    user, err = _game_or_401(db)
    if err:
        return err
    m = _market(db)
    b = next((x for x in m["barters"] if x["id"] == (request.json or {}).get('id')), None)
    if not b:
        return jsonify({"success": False, "message": "Tin trao đổi này không còn nữa!"})
    if b["seller"] == user["username"]:
        return jsonify({"success": False, "message": "Không thể tự chấp nhận tin đổi của mình!"})
    w = b["want"]
    if not _item_remove(user, w["kind"], w["key"], w["qty"]):
        return jsonify({"success": False, "message": f"Đạo hữu không có đủ {w['label']} ×{w['qty']} để đổi!"})
    seller = find_user(db, b["seller"])
    gv = b["give"]
    _item_add(user, gv["kind"], gv["key"], gv["qty"])
    if seller:
        _item_add(seller, w["kind"], w["key"], w["qty"])
        _notify(seller, f"🔁 {user.get('author_name', user['username'])} đã chấp nhận trao đổi: đạo hữu nhận {w['label']} ×{w['qty']}, đổi lại {gv['label']} ×{gv['qty']}.")
        _log(seller, f"Trao đổi thành công: nhận {w['label']} ×{w['qty']}")
    _deal(db, f"🔁 {user.get('author_name', user['username'])} đổi {w['label']} ×{w['qty']} lấy {gv['label']} ×{gv['qty']} của {b['seller_name']}.")
    m["barters"].remove(b)
    save_database(db, f"@{user['username']} chap nhan trao doi {b['id']}")
    return _cho_resp(db, user, f"🔁 Trao đổi thành công! Đạo hữu nhận {gv['label']} ×{gv['qty']}, đưa đi {w['label']} ×{w['qty']}.")


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
    elif action == "reset_chibi":
        g["chibi"] = None
        _notify(target, "🧒 Chưởng Môn Nhân đã mở khóa chọn lại nhân vật chibi cho đạo hữu. Vào Động Phủ để chọn nhân vật mới (chọn xong sẽ khóa vĩnh viễn như cũ).")
        msg = f"Đã mở khóa chọn lại nhân vật chibi cho [{target['username']}]."
    elif action == "max_full":
        # Nâng FULL cho tài khoản chủ theo yêu cầu ông chủ: điểm, cấp độ, linh thạch, bảo vật, linh sủng
        _add_diem(target, 999999999 - int(g.get("diem", 0)), "Nâng cấp FULL tài khoản chủ")
        g["exp"] = 1000000
        g["tu_vi"] = {"muc": 12, "cap": 3, "break_missions": 0}
        g["linh_khi"] = 999999999
        for loai in STONE_TYPES:
            g["linh_thach"][loai] = 99999
        g["inventory"]["treasures"] = [t["id"] for t in TREASURES]
        _nghe(target)["thu"] = {"ten": "Hắc Kỳ Lân Con", "exp": 999}
        g["capacity"] = -1
        _log(target, "Tài khoản chủ được nâng FULL: Điểm Cống Hiến / Linh Thạch / Tu Vi / Bảo Vật / Linh Sủng tối đa")
        _notify(target, "🚀 Tài khoản của đạo hữu đã được nâng FULL: Điểm Cống Hiến, Linh Thạch, Tu Vi Đại Chúa Tể, đủ 9 bảo vật và Linh Sủng cấp tối đa!")
        msg = f"Đã nâng FULL cho [{target['username']}]: 999.999.999 điểm · Tu Vi Đại Chúa Tể · Viên Mãn · linh thạch 99.999 mỗi loại · đủ 9 bảo vật · linh sủng cấp 10 · túi không giới hạn."
    else:
        return jsonify({"success": False, "message": "Hành động không hợp lệ!"})
    save_database(db, f"Admin @{admin['username']} action {action} -> @{target['username']}")
    return jsonify({"success": True, "message": msg})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
