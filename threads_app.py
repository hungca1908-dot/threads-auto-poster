import streamlit as st
import requests
import base64
import time
import re
import json
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

st.set_page_config(page_title="Threads Auto Publisher", page_icon="🧵", layout="centered")
BASE_URL = "https://graph.threads.net/v1.0"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DATA_FILE = "schedule_data.json"
FILE_LOCK = threading.Lock()


def now_vn() -> datetime:
    """Lấy thời gian hiện tại chuẩn múi giờ Việt Nam (bỏ tzinfo để so sánh đồng nhất)."""
    return datetime.now(VN_TZ).replace(tzinfo=None)


def load_data() -> dict:
    """Đọc dữ liệu cấu hình và hàng đợi từ file JSON."""
    with FILE_LOCK:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"user_id": "me", "access_token": "", "imgbb_key": "", "posts": []}


def save_data(data: dict):
    """Lưu dữ liệu xuống file JSON để không bị mất khi tắt trình duyệt."""
    with FILE_LOCK:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def extract_clean_urls(raw_text: str) -> list:
    if not raw_text.strip():
        return []
    bbcode_links = re.findall(r"\[img\](https?://[^\s\]]+)\[/img\]", raw_text, flags=re.IGNORECASE)
    if bbcode_links:
        return [link.strip() for link in bbcode_links]

    urls = []
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            urls.append(line)
    return urls


def upload_to_imgbb(image_file, api_key: str) -> str:
    url = "https://api.imgbb.com/1/upload"
    encoded_image = base64.b64encode(image_file.getvalue()).decode("utf-8")
    payload = {"key": api_key.strip(), "image": encoded_image}
    res = requests.post(url, data=payload).json()
    if res.get("success"):
        return res["data"]["url"]
    raise Exception(f"Lỗi upload ảnh lên ImgBB: {res.get('error', res)}")


def publish_to_threads(user_id: str, token: str, text: str, image_urls: list):
    uid = user_id.strip()
    tk = token.strip()
    create_url = f"{BASE_URL}/{uid}/threads"

    if len(image_urls) == 0:
        payload = {"media_type": "TEXT", "text": text, "access_token": tk}
        res_create = requests.post(create_url, data=payload).json()
        if "id" not in res_create:
            return False, f"Lỗi tạo container văn bản: {res_create.get('error', res_create)}"
        creation_id = res_create["id"]
        time.sleep(2)

    elif len(image_urls) == 1:
        payload = {"media_type": "IMAGE", "image_url": image_urls[0], "text": text, "access_token": tk}
        res_create = requests.post(create_url, data=payload).json()
        if "id" not in res_create:
            return False, f"Lỗi tạo container ảnh: {res_create.get('error', res_create)}"
        creation_id = res_create["id"]
        time.sleep(5)

    else:
        if len(image_urls) > 20:
            return False, "Threads API chỉ cho phép tối đa 20 ảnh trong 1 bài viết."
        children_ids = []
        for idx, img_url in enumerate(image_urls):
            child_payload = {
                "media_type": "IMAGE",
                "image_url": img_url,
                "is_carousel_item": "true",
                "access_token": tk,
            }
            res_child = requests.post(create_url, data=child_payload).json()
            if "id" not in res_child:
                return False, f"Lỗi tạo ảnh thứ {idx + 1}: {res_child.get('error', res_child)}"
            children_ids.append(res_child["id"])
            time.sleep(1.5)

        carousel_payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
            "text": text,
            "access_token": tk,
        }
        res_carousel = requests.post(create_url, data=carousel_payload).json()
        if "id" not in res_carousel:
            return False, f"Lỗi tạo album Carousel: {res_carousel.get('error', res_carousel)}"
        creation_id = res_carousel["id"]
        time.sleep(6)

    publish_url = f"{BASE_URL}/{uid}/threads_publish"
    res_publish = requests.post(publish_url, data={"creation_id": creation_id, "access_token": tk}).json()

    if "id" in res_publish:
        return True, f"Đăng thành công ({len(image_urls)} ảnh)! Post ID: {res_publish['id']}"
    return False, f"Lỗi xuất bản: {res_publish.get('error', res_publish)}"


# ================= TIẾN TRÌNH CHẠY NGẦM 24/7 TRÊN SERVER =================
def background_scheduler_worker():
    """Chạy độc lập trên server ngay cả khi người dùng đã đóng trình duyệt và tắt máy."""
    while True:
        try:
            data = load_data()
            posts = data.get("posts", [])
            uid = data.get("user_id") or st.secrets.get("THREADS_USER_ID", "me")
            tk = data.get("access_token") or st.secrets.get("THREADS_ACCESS_TOKEN", "")

            if tk and any(p.get("status") == "Đang chờ" for p in posts):
                current_time = now_vn()
                updated = False
                for item in posts:
                    if item.get("status") == "Đang chờ":
                        run_at_dt = datetime.fromisoformat(item["run_at"])
                        if current_time >= run_at_dt:
                            success, msg = publish_to_threads(uid, tk, item["text"], item["image_urls"])
                            item["status"] = "✅ Đã đăng" if success else f"❌ Lỗi: {msg}"
                            updated = True
                if updated:
                    data["posts"] = posts
                    save_data(data)
        except Exception:
            pass
        time.sleep(10)


@st.cache_resource
def start_background_worker():
    t = threading.Thread(target=background_scheduler_worker, daemon=True)
    t.start()
    return True


start_background_worker()

# ================= TẢI DỮ LIỆU & CẤU HÌNH =================
app_data = load_data()
default_uid = app_data.get("user_id") or st.secrets.get("THREADS_USER_ID", "me")
default_token = app_data.get("access_token") or st.secrets.get("THREADS_ACCESS_TOKEN", "")
default_imgbb = app_data.get("imgbb_key") or st.secrets.get("IMGBB_API_KEY", "")

with st.sidebar:
    st.header("⚙️ Cấu hình API")
    user_id = st.text_input("Threads User ID", value=default_uid)
    access_token = st.text_input("Threads Access Token", value=default_token, type="password")
    imgbb_key = st.text_input("ImgBB API Key (Nếu upload từ máy)", value=default_imgbb, type="password")

    if st.button("💾 Lưu cấu hình lên Server", use_container_width=True):
        app_data["user_id"] = user_id.strip()
        app_data["access_token"] = access_token.strip()
        app_data["imgbb_key"] = imgbb_key.strip()
        save_data(app_data)
        st.success("Đã lưu cấu hình cho tiến trình chạy ngầm!")

    st.divider()
    st.caption(f"🇻🇳 Giờ hệ thống: **{now_vn().strftime('%H:%M:%S - %d/%m/%Y')}**")


def get_base_schedule_time(posts: list) -> tuple[datetime, bool]:
    current_vn = now_vn()
    pending_times = [
        datetime.fromisoformat(p["run_at"])
        for p in posts
        if p.get("status") == "Đang chờ" and datetime.fromisoformat(p["run_at"]) > current_vn
    ]
    if pending_times:
        return max(pending_times), True
    return current_vn, False


# ================= GIAO DIỆN CHÍNH =================
st.title("🧵 Threads Auto Poster (Cloud 24/7)")
st.caption("Tự động chạy ngầm trên máy chủ đám mây kể cả khi tắt máy tính")

pending_count = sum(1 for p in app_data["posts"] if p.get("status") == "Đang chờ")
tab_compose, tab_queue = st.tabs(
    ["✍️ Soạn & Đăng bài", f"📅 Hàng đợi hẹn giờ ({pending_count} đang chờ)"]
)

with tab_compose:
    post_text = st.text_area("Nội dung bài viết", height=140, max_chars=500, placeholder="Bạn đang nghĩ gì?")

    img_source = st.radio(
        "Nguồn hình ảnh:",
        ["Không dùng ảnh", "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)", "Chọn nhiều ảnh từ máy tính"],
        horizontal=True,
    )

    uploaded_files = []
    raw_urls_input = ""

    if img_source == "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)":
        raw_urls_input = st.text_area(
            "Dán link ảnh HTTPS (mỗi link 1 dòng, hoặc dán thẳng BBCode từ Postimages)",
            height=130,
            placeholder="https://i.postimg.cc/anh1.jpg\nhttps://i.postimg.cc/anh2.jpg",
        )
        preview_urls = extract_clean_urls(raw_urls_input)
        if preview_urls:
            st.caption(f"Đã nhận diện **{len(preview_urls)}** ảnh hợp lệ:")
            cols = st.columns(min(len(preview_urls), 4))
            for i, u in enumerate(preview_urls[:8]):
                cols[i % 4].image(u, use_container_width=True)

    elif img_source == "Chọn nhiều ảnh từ máy tính":
        uploaded_files = st.file_uploader(
            "Chọn tối đa 20 ảnh (JPG, PNG, WEBP)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )
        if uploaded_files:
            st.caption(f"Đã chọn **{len(uploaded_files)}** ảnh:")
            cols = st.columns(min(len(uploaded_files), 4))
            for i, f in enumerate(uploaded_files[:8]):
                cols[i % 4].image(f, use_container_width=True)

    st.divider()
    post_mode = st.radio("Chế độ đăng:", ["Đăng ngay bây giờ", "Hẹn giờ đăng"], horizontal=True)

    def resolve_final_image_list() -> list:
        if img_source == "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)":
            return extract_clean_urls(raw_urls_input)
        elif img_source == "Chọn nhiều ảnh từ máy tính" and uploaded_files:
            if not imgbb_key.strip():
                raise Exception("Vui lòng nhập ImgBB API Key ở thanh bên trái để upload ảnh từ máy!")
            if len(uploaded_files) > 20:
                raise Exception("Chỉ được chọn tối đa 20 ảnh cho 1 bài viết.")
            return [upload_to_imgbb(f, imgbb_key) for f in uploaded_files]
        return []

    if post_mode == "Đăng ngay bây giờ":
        if st.button("🚀 Đăng bài ngay", type="primary", use_container_width=True):
            if not access_token.strip():
                st.error("Vui lòng nhập Threads Access Token!")
            elif not post_text.strip() and img_source == "Không dùng ảnh":
                st.warning("Vui lòng nhập nội dung hoặc thêm ít nhất 1 hình ảnh.")
            else:
                try:
                    with st.spinner("Đang tạo container và xuất bản lên Threads..."):
                        final_urls = resolve_final_image_list()
                        success, msg = publish_to_threads(user_id, access_token, post_text, final_urls)
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)
                except Exception as e:
                    st.error(str(e))
    else:
        base_time, has_queue = get_base_schedule_time(app_data["posts"])
        stack_mode = True
        if has_queue:
            stack_mode = st.checkbox(
                f"Cộng dồn tiếp vào bài cuối trong hàng đợi (Mốc gốc: {base_time.strftime('%H:%M:%S - %d/%m')})",
                value=True,
            )

        delay_option = st.radio(
            "Chọn mốc thời gian:",
            ["+ 5 phút", "+ 10 phút", "+ 15 phút", "+ 30 phút", "+ 60 phút", "Tùy chỉnh ngày & giờ"],
            horizontal=True,
        )

        minute_map = {"+ 5 phút": 5, "+ 10 phút": 10, "+ 15 phút": 15, "+ 30 phút": 30, "+ 60 phút": 60}
        custom_date = base_time.date()
        custom_time = (base_time + timedelta(minutes=5)).time()

        if delay_option == "Tùy chỉnh ngày & giờ":
            col1, col2 = st.columns(2)
            with col1:
                custom_date = st.date_input("Chọn ngày", value=custom_date)
            with col2:
                custom_time = st.time_input("Chọn giờ", value=custom_time)
            preview_run_at = datetime.combine(custom_date, custom_time)
        else:
            anchor = base_time if (has_queue and stack_mode) else now_vn()
            preview_run_at = anchor + timedelta(minutes=minute_map[delay_option])

        st.info(f"🕒 Thời gian dự kiến xuất bản (Giờ VN): **{preview_run_at.strftime('%H:%M:%S - %d/%m/%Y')}**")

        if st.button("⏰ Thêm vào lịch đăng", type="primary", use_container_width=True):
            if not access_token.strip():
                st.error("Vui lòng nhập Threads Access Token!")
            elif not post_text.strip() and img_source == "Không dùng ảnh":
                st.warning("Vui lòng nhập nội dung hoặc thêm ít nhất 1 hình ảnh.")
            else:
                current_vn = now_vn()
                fresh_data = load_data()
                if delay_option in minute_map:
                    latest_base, latest_has_queue = get_base_schedule_time(fresh_data["posts"])
                    anchor = latest_base if (latest_has_queue and stack_mode) else current_vn
                    run_at = anchor + timedelta(minutes=minute_map[delay_option])
                else:
                    run_at = datetime.combine(custom_date, custom_time)

                if run_at <= current_vn:
                    st.warning("Thời gian hẹn giờ phải ở trong tương lai!")
                else:
                    try:
                        with st.spinner("Đang lưu bài viết vào hàng đợi Cloud..."):
                            final_urls = resolve_final_image_list()
                            fresh_data["user_id"] = user_id.strip()
                            fresh_data["access_token"] = access_token.strip()
                            fresh_data["imgbb_key"] = imgbb_key.strip()
                            fresh_data["posts"].append({
                                "text": post_text,
                                "image_urls": final_urls,
                                "run_at": run_at.isoformat(),
                                "status": "Đang chờ",
                            })
                            save_data(fresh_data)
                            st.success(f"Đã lên lịch đăng ({len(final_urls)} ảnh) lúc **{run_at.strftime('%H:%M:%S - %d/%m/%Y')}**!")
                            time.sleep(1)
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

with tab_queue:
    if st.button("🔄 Làm mới trạng thái hàng đợi"):
        st.rerun()

    latest_data = load_data()
    if not latest_data["posts"]:
        st.info("Chưa có bài viết nào trong hàng đợi.")
    else:
        st.success("✅ Tiến trình ngầm đang hoạt động. Bạn có thể đóng trình duyệt hoặc tắt máy tính, hệ thống vẫn tự động đăng đúng giờ.")
        sorted_posts = sorted(enumerate(latest_data["posts"]), key=lambda x: x[1]["run_at"])

        for orig_idx, item in sorted_posts:
            run_at_dt = datetime.fromisoformat(item["run_at"])
            with st.container(border=True):
                col_info, col_action = st.columns([4, 1])
                with col_info:
                    time_left = ""
                    if item["status"] == "Đang chờ":
                        diff = int((run_at_dt - now_vn()).total_seconds())
                        if diff > 0:
                            mins, secs = divmod(diff, 60)
                            time_left = f" (Còn {mins}p {secs}s)"
                    st.markdown(
                        f"**🕒 Thời gian:** `{run_at_dt.strftime('%H:%M:%S - %d/%m/%Y')}`{time_left} | **Trạng thái:** `{item['status']}`"
                    )
                    st.write(item["text"])
                    if item["image_urls"]:
                        st.caption(f"🖼️ Đính kèm {len(item['image_urls'])} ảnh")
                with col_action:
                    if st.button("Xóa", key=f"del_{orig_idx}"):
                        latest_data["posts"].pop(orig_idx)
                        save_data(latest_data)
                        st.rerun()

    # & 'C:\Users\LAPTOP\AppData\Local\Programs\Python\Python314\python.exe' -m streamlit run "c:/Users/LAPTOP/OneDrive/Desktop/THREAD TOOL/threads_app.py"