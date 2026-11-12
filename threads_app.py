import streamlit as st
import requests
import base64
import time
import re
import json
import os
import random
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


def get_last_queued_time(posts: list) -> tuple[datetime, bool]:
    current_vn = now_vn()
    pending_times = [
        datetime.fromisoformat(p["run_at"])
        for p in posts
        if p.get("status") == "Đang chờ" and datetime.fromisoformat(p["run_at"]) > current_vn
    ]
    if pending_times:
        return max(pending_times), True
    return current_vn, False


# ================= GIAO DIỆN CHÍNH (GIỮ NGUYÊN TRANG KHI XÓA/SỬA) =================
st.title("🧵 Threads Auto Poster (Cloud 24/7)")
st.caption("Tự động chạy ngầm trên máy chủ đám mây kể cả khi tắt máy tính")

if "active_page" not in st.session_state:
    st.session_state.active_page = "compose"

pending_count = sum(1 for p in app_data["posts"] if p.get("status") == "Đang chờ")

nav_c1, nav_c2 = st.columns(2)
with nav_c1:
    if st.button(
        "✍️ Soạn & Đăng bài",
        type="primary" if st.session_state.active_page == "compose" else "secondary",
        use_container_width=True,
    ):
        st.session_state.active_page = "compose"
        st.rerun()
with nav_c2:
    if st.button(
        f"📅 Quản lý hàng đợi ({pending_count} đang chờ)",
        type="primary" if st.session_state.active_page == "queue" else "secondary",
        use_container_width=True,
    ):
        st.session_state.active_page = "queue"
        st.rerun()

st.divider()

# ================= TRANG 1: SOẠN & ĐĂNG BÀI =================
if st.session_state.active_page == "compose":
    post_text = st.text_area("Nội dung bài viết", height=140, max_chars=500, placeholder="Bạn đang nghĩ gì?")

    img_source = st.radio(
        "Nguồn hình ảnh:",
        ["Không dùng ảnh", "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)", "Chọn nhiều ảnh từ máy tính"],
        horizontal=True,
    )

    uploaded_files = []
    raw_urls_input = ""
    total_available_images = 0

    if img_source == "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)":
        raw_urls_input = st.text_area(
            "Dán link ảnh HTTPS (mỗi link 1 dòng, hoặc dán thẳng BBCode từ Postimages)",
            height=130,
            placeholder="https://i.postimg.cc/anh1.jpg\nhttps://i.postimg.cc/anh2.jpg",
        )
        preview_urls = extract_clean_urls(raw_urls_input)
        total_available_images = len(preview_urls)
        if preview_urls:
            st.caption(f"Đã nhận diện **{total_available_images}** ảnh hợp lệ trong kho:")
            cols = st.columns(min(total_available_images, 4))
            for i, u in enumerate(preview_urls[:8]):
                cols[i % 4].image(u, use_container_width=True)

    elif img_source == "Chọn nhiều ảnh từ máy tính":
        uploaded_files = st.file_uploader(
            "Chọn ảnh từ máy/điện thoại (JPG, PNG, WEBP)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )
        total_available_images = len(uploaded_files) if uploaded_files else 0
        if uploaded_files:
            st.caption(f"Đã chọn **{total_available_images}** ảnh vào kho:")
            cols = st.columns(min(total_available_images, 4))
            for i, f in enumerate(uploaded_files[:8]):
                cols[i % 4].image(f, use_container_width=True)

    # TÙY CHỌN CHỌN NGẪU NHIÊN (RANDOM) SỐ LƯỢNG ẢNH (VÍ DỤ: 2/7 HÌNH)
    use_random_images = False
    random_pick_count = 2
    if img_source != "Không dùng ảnh" and total_available_images > 1:
        with st.container(border=True):
            use_random_images = st.checkbox(
                f"🎲 Chọn ngẫu nhiên (Random) một số ảnh từ {total_available_images} ảnh ở trên",
                value=False,
            )
            if use_random_images:
                max_pick = min(total_available_images, 20)
                default_pick = min(2, max_pick)
                random_pick_count = st.slider(
                    "Số lượng ảnh lấy ngẫu nhiên mỗi lần đăng:",
                    min_value=1,
                    max_value=max_pick,
                    value=default_pick,
                )
                st.caption(
                    f"✨ Hệ thống sẽ bốc ngẫu nhiên **{random_pick_count}/{total_available_images}** hình mỗi lần bạn bấm Đăng hoặc Thêm vào lịch."
                )

    st.divider()
    post_mode = st.radio("Chế độ đăng:", ["Đăng ngay bây giờ", "Hẹn giờ đăng"], horizontal=True)

    def resolve_final_image_list() -> list:
        if img_source == "Dán danh sách link ảnh (Hỗ trợ nhiều ảnh)":
            urls = extract_clean_urls(raw_urls_input)
            if use_random_images and len(urls) > random_pick_count:
                return random.sample(urls, random_pick_count)
            return urls
        elif img_source == "Chọn nhiều ảnh từ máy tính" and uploaded_files:
            if not imgbb_key.strip():
                raise Exception("Vui lòng nhập ImgBB API Key ở thanh bên trái để upload ảnh từ máy!")
            selected_files = list(uploaded_files)
            if use_random_images and len(selected_files) > random_pick_count:
                selected_files = random.sample(selected_files, random_pick_count)
            if len(selected_files) > 20:
                raise Exception("Chỉ được chọn tối đa 20 ảnh cho 1 bài viết.")
            return [upload_to_imgbb(f, imgbb_key) for f in selected_files]
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
        last_queued_dt, has_queue = get_last_queued_time(app_data["posts"])

        anchor_options = ["Giờ hiện tại", "Tự chọn mốc ngày & giờ gốc"]
        if has_queue:
            anchor_options.insert(0, "Bài cuối trong hàng đợi")

        anchor_choice = st.radio("1️⃣ Chọn mốc thời gian gốc:", anchor_options, horizontal=True)

        if anchor_choice == "Bài cuối trong hàng đợi":
            anchor_dt = last_queued_dt
            st.caption(f"📌 Mốc gốc đang lấy theo bài cuối hàng đợi: **{anchor_dt.strftime('%H:%M:%S - %d/%m/%Y')}**")
        elif anchor_choice == "Giờ hiện tại":
            anchor_dt = now_vn()
            st.caption(f"📌 Mốc gốc đang lấy theo giờ hiện tại: **{anchor_dt.strftime('%H:%M:%S - %d/%m/%Y')}**")
        else:
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                anchor_date = st.date_input("Ngày mốc gốc", value=last_queued_dt.date(), key="anchor_date")
            with col_a2:
                anchor_time = st.time_input("Giờ mốc gốc", value=last_queued_dt.time(), key="anchor_time")
            anchor_dt = datetime.combine(anchor_date, anchor_time)

        delay_option = st.radio(
            "2️⃣ Cộng thêm thời gian từ mốc gốc:",
            ["+ 0 phút (Đúng mốc gốc)", "+ 5 phút", "+ 10 phút", "+ 15 phút", "+ 30 phút", "+ 60 phút"],
            index=1,
            horizontal=True,
        )

        minute_map = {
            "+ 0 phút (Đúng mốc gốc)": 0,
            "+ 5 phút": 5,
            "+ 10 phút": 10,
            "+ 15 phút": 15,
            "+ 30 phút": 30,
            "+ 60 phút": 60,
        }

        preview_run_at = anchor_dt + timedelta(minutes=minute_map[delay_option])
        st.info(f"🕒 Thời gian dự kiến xuất bản (Giờ VN): **{preview_run_at.strftime('%H:%M:%S - %d/%m/%Y')}**")

        if st.button("⏰ Thêm vào lịch đăng", type="primary", use_container_width=True):
            if not access_token.strip():
                st.error("Vui lòng nhập Threads Access Token!")
            elif not post_text.strip() and img_source == "Không dùng ảnh":
                st.warning("Vui lòng nhập nội dung hoặc thêm ít nhất 1 hình ảnh.")
            else:
                current_vn = now_vn()
                fresh_data = load_data()

                if anchor_choice == "Bài cuối trong hàng đợi":
                    latest_last, latest_has_q = get_last_queued_time(fresh_data["posts"])
                    final_anchor = latest_last if latest_has_q else current_vn
                elif anchor_choice == "Giờ hiện tại":
                    final_anchor = current_vn
                else:
                    final_anchor = anchor_dt

                run_at = final_anchor + timedelta(minutes=minute_map[delay_option])

                if run_at <= current_vn:
                    st.warning("Thời gian hẹn giờ phải ở trong tương lai! Hãy chỉnh lại mốc gốc hoặc cộng thêm phút.")
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

# ================= TRANG 2: QUẢN LÝ HÀNG ĐỢI HẸN GIỜ =================
else:
    latest_data = load_data()
    all_posts = latest_data.get("posts", [])

    cnt_waiting = sum(1 for p in all_posts if p.get("status") == "Đang chờ")
    cnt_done = sum(1 for p in all_posts if str(p.get("status", "")).startswith("✅"))
    cnt_err = sum(1 for p in all_posts if str(p.get("status", "")).startswith("❌"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📋 Tổng số bài", len(all_posts))
    m2.metric("⏳ Đang chờ", cnt_waiting)
    m3.metric("✅ Đã đăng", cnt_done)
    m4.metric("❌ Bị lỗi", cnt_err)

    st.markdown("**⚡ Thao tác nhanh:**")
    b1, b2, b3, b4 = st.columns(4)

    with b1:
        if st.button("🔄 Làm mới", use_container_width=True):
            st.rerun()

    with b2:
        if st.button("🚀 Đăng hết bài chờ", use_container_width=True, disabled=(cnt_waiting == 0)):
            if not access_token.strip():
                st.error("Thiếu Threads Access Token!")
            else:
                with st.spinner("Đang xuất bản toàn bộ bài đang chờ..."):
                    for item in latest_data["posts"]:
                        if item.get("status") == "Đang chờ":
                            ok, msg = publish_to_threads(user_id, access_token, item["text"], item["image_urls"])
                            item["status"] = "✅ Đã đăng" if ok else f"❌ Lỗi: {msg}"
                            save_data(latest_data)
                            time.sleep(3)
                st.rerun()

    with b3:
        if st.button("🧹 Dọn bài đã xong/lỗi", use_container_width=True, disabled=(cnt_done + cnt_err == 0)):
            latest_data["posts"] = [p for p in latest_data["posts"] if p.get("status") == "Đang chờ"]
            save_data(latest_data)
            st.rerun()

    with b4:
        if st.button("🗑️ Xóa tất cả", use_container_width=True, disabled=(len(all_posts) == 0)):
            latest_data["posts"] = []
            save_data(latest_data)
            st.rerun()

    st.divider()

    filter_status = st.radio(
        "Lọc hiển thị:",
        ["Tất cả", "⏳ Đang chờ", "✅ Đã đăng", "❌ Bị lỗi"],
        horizontal=True,
    )

    if not all_posts:
        st.info("Chưa có bài viết nào trong hàng đợi.")
    else:
        sorted_posts = sorted(enumerate(all_posts), key=lambda x: x[1]["run_at"])
        displayed_count = 0

        for orig_idx, item in sorted_posts:
            status = item.get("status", "Đang chờ")
            if filter_status == "⏳ Đang chờ" and status != "Đang chờ":
                continue
            if filter_status == "✅ Đã đăng" and not status.startswith("✅"):
                continue
            if filter_status == "❌ Bị lỗi" and not status.startswith("❌"):
                continue

            displayed_count += 1
            run_at_dt = datetime.fromisoformat(item["run_at"])

            with st.container(border=True):
                time_left = ""
                if status == "Đang chờ":
                    diff = int((run_at_dt - now_vn()).total_seconds())
                    if diff > 0:
                        hours, rem = divmod(diff, 3600)
                        mins, secs = divmod(rem, 60)
                        time_left = f" — *(Còn {hours}h {mins}p {secs}s)*" if hours > 0 else f" — *(Còn {mins}p {secs}s)*"
                    else:
                        time_left = " — *(Đang xử lý đăng...)*"

                st.markdown(
                    f"**#{displayed_count} | 🕒 `{run_at_dt.strftime('%H:%M:%S - %d/%m/%Y')}`**{time_left} | `{status}`"
                )

                if item.get("text"):
                    st.write(item["text"])
                else:
                    st.caption("*(Bài viết chỉ có hình ảnh, không có văn bản)*")

                imgs = item.get("image_urls", [])
                if imgs:
                    with st.expander(f"🖼️ Xem {len(imgs)} ảnh đính kèm"):
                        img_cols = st.columns(min(len(imgs), 4))
                        for i, u in enumerate(imgs):
                            img_cols[i % 4].image(u, use_container_width=True)

                c1, c2, c3, c4 = st.columns(4)

                with c1:
                    if status == "Đang chờ":
                        if st.button("🚀 Đăng ngay", key=f"pubnow_{orig_idx}", use_container_width=True):
                            with st.spinner("Đang đăng bài..."):
                                ok, msg = publish_to_threads(user_id, access_token, item["text"], imgs)
                                latest_data["posts"][orig_idx]["status"] = "✅ Đã đăng" if ok else f"❌ Lỗi: {msg}"
                                save_data(latest_data)
                                st.rerun()
                    elif status.startswith("❌"):
                        if st.button("🔄 Thử lại", key=f"retry_{orig_idx}", use_container_width=True):
                            latest_data["posts"][orig_idx]["status"] = "Đang chờ"
                            latest_data["posts"][orig_idx]["run_at"] = (now_vn() + timedelta(seconds=15)).isoformat()
                            save_data(latest_data)
                            st.rerun()

                with c2:
                    if status == "Đang chờ":
                        if st.button("⏩ Dời +15p", key=f"delay15_{orig_idx}", use_container_width=True):
                            latest_data["posts"][orig_idx]["run_at"] = (run_at_dt + timedelta(minutes=15)).isoformat()
                            save_data(latest_data)
                            st.rerun()

                with c3:
                    if status == "Đang chờ":
                        edit_key = f"editing_{orig_idx}"
                        if edit_key not in st.session_state:
                            st.session_state[edit_key] = False
                        if st.button("✏️ Sửa bài", key=f"editbtn_{orig_idx}", use_container_width=True):
                            st.session_state[edit_key] = not st.session_state[edit_key]
                            st.rerun()

                with c4:
                    if st.button("🗑️ Xóa", key=f"del_{orig_idx}", use_container_width=True):
                        latest_data["posts"].pop(orig_idx)
                        save_data(latest_data)
                        st.rerun()

                if status == "Đang chờ" and st.session_state.get(f"editing_{orig_idx}", False):
                    st.divider()
                    new_text = st.text_area(
                        "Sửa nội dung:",
                        value=item.get("text", ""),
                        key=f"newtxt_{orig_idx}",
                        max_chars=500,
                    )
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        new_date = st.date_input("Sửa ngày:", value=run_at_dt.date(), key=f"newd_{orig_idx}")
                    with ec2:
                        new_time = st.time_input("Sửa giờ:", value=run_at_dt.time(), key=f"newt_{orig_idx}")

                    sc1, sc2 = st.columns(2)
                    with sc1:
                        if st.button("💾 Lưu thay đổi", key=f"saveedit_{orig_idx}", type="primary", use_container_width=True):
                            updated_dt = datetime.combine(new_date, new_time)
                            latest_data["posts"][orig_idx]["text"] = new_text
                            latest_data["posts"][orig_idx]["run_at"] = updated_dt.isoformat()
                            save_data(latest_data)
                            st.session_state[f"editing_{orig_idx}"] = False
                            st.rerun()
                    with sc2:
                        if st.button("Hủy", key=f"canceledit_{orig_idx}", use_container_width=True):
                            st.session_state[f"editing_{orig_idx}"] = False
                            st.rerun()

        if displayed_count == 0:
            st.info(f"Không có bài viết nào ở mục lọc **{filter_status}**.")
