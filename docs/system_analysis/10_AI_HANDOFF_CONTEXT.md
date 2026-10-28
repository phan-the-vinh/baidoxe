# TÀI LIỆU BÀN GIAO NGỮ CẢNH DÀNH RIÊNG CHO AI TIẾP NHẬN (AI HANDOFF CONTEXT)

> **Tài liệu ưu tiên cao nhất (Top Priority Document)**  
> **Dành cho:** AI Agent / Kỹ sư phần mềm tiếp nhận dự án Parking AI  
> **Mục tiêu:** Cung cấp đầy đủ toàn bộ ngữ cảnh kỹ thuật, nghiệp vụ, kiến trúc, quy tắc bất di bất dịch và các điểm nguy hiểm mà không cần phải đọc lại toàn bộ mã nguồn từ đầu.  

---

## 1. Bản Chất Dự Án và Trạng Thái Thực Tế

- **Tên dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI).
- **Ngôn ngữ & Framework:** Python 3.11, Flask 3.1.2 (Application Factory Pattern), Waitress WSGI Server (`threads=6`), Microsoft SQL Server (Transact-SQL), SQLAlchemy 2.0.43.
- **Trạng thái thực tế:**
  - Dự án **KHÔNG PHẢI** là Git repository (chỉ là thư mục mã nguồn độc lập).
  - Dự án đang ở **trạng thái chuyển giao kép (Dual-State / Transitional Phase)**:
    - *Tài liệu mô tả cũ:* Bãi 20 ô Khu A/B (`A01`–`B10`), dùng Keras CNN from-scratch 96x96, không dùng YOLO.
    - *Mã nguồn hoạt động thực tế:* Đã nâng cấp hỗ trợ bãi 24 ô Khu D (`D01`–`D24`). Seed CSDL mới tự động tạo 24 ô Khu D. Mô hình CNN cũ bị vô hiệu hóa trên bãi D24. Thay vào đó, hệ thống sử dụng pipeline **YOLOv8n Car Detector + 24 Polygon ROI** (`tools/analyze_raw_parking_video.py`, `app/services/vision_video.py`) và phát hiện biển số 2 giai đoạn (Car YOLO + Plate YOLO + Tesseract OCR).
    - Có sẵn bộ dữ liệu video demo đã xử lý đầy đủ (`runs/detect/polygon_tests/.../full_video_d16_logic_fix_conf040/yolo_polygon_annotated_h264.mp4` và các tệp CSV timeline trạng thái).

---

## 2. Bản Đồ Tệp Tin Cốt Lõi (Phải Đọc Trước Khi Sửa Bất Kỳ Thứ Gì)

Trước khi chỉnh sửa bất kỳ tính năng nào, bạn **BẮT BUỘC** phải đọc các file sau:

1. `app/__init__.py`: Application factory (`create_app`), đăng ký Blueprints và các bộ xử lý lỗi tập trung.
2. `app/models.py`: 13 Models CSDL, các filtered unique indexes và CHECK constraints quan trọng.
3. `app/routes.py`: Toàn bộ các web route và REST API endpoints (445 dòng code).
4. `app/services/common.py`: Các hàm dùng chung sống còn: `write_lock()`, `normalize_plate()`, `audit()`, `field()`, `@manager_only`.
5. `app/services/parking.py`: Toàn bộ nghiệp vụ check-in, check-out, quote, move_visit, monthly.
6. `app/services/billing.py`: Thuật toán tính phí lũy tiến block 2h và phụ thu qua đêm.
7. `app/services/parking_layout.py`: Hàm `current_layout()` kiểm tra CSDL đang chạy layout 20 ô (A/B) hay 24 ô (D).
8. `app/services/chatbot.py`: Kiến trúc an toàn của Chatbot (Intent Whitelist, Zero SQL, Grounded verified data).
9. `app/services/vision_video.py`: Quản lý job phân tích video bãi xe chạy nền.
10. `tools/yolo_polygon_logic.py`: Thuật toán tính diện tích giao nhau và bộ lọc trạng thái thời gian (Temporal Smoothing).

---

## 3. Các Quyết Định Kiến Trúc Đã Được Xác Minh (Proven Architectural Decisions)

1. **Tuần tự hóa giao dịch bằng Khóa bi quan toàn cục (`write_lock`):**
   - Vị trí: `app/services/common.py:L68-82`.
   - Cơ chế: `SELECT SiteLock WHERE id=1 WITH (UPDLOCK, HOLDLOCK)`.
   - Mục đích: Ngăn chặn 100% tình trạng hai xe cùng vào một ô đỗ (Double-booking) hoặc một xe tạo hai lượt gửi cùng lúc.
   - **Quy tắc cho AI:** Mọi hàm sửa đổi dữ liệu (mutation) trong tầng service **bắt buộc phải gọi `write_lock()` trước câu truy vấn đọc dữ liệu đầu tiên**.
2. **Bảo vệ toàn vẹn bằng Filtered Unique Indexes trong SQL Server:**
   - `uq_active_plate`: Đảm bảo một biển số chỉ có tối đa một lượt gửi chưa ra (`WHERE exited_at IS NULL`).
   - `uq_active_slot`: Đảm bảo một ô đỗ chỉ chứa tối đa một xe (`WHERE exited_at IS NULL`).
   - `uq_payment_visit`: Chống thanh toán hai lần cho cùng một lượt gửi.
3. **Mô hình Bảng giá Append-Only:**
   - Bảng `rates` không bao giờ update dòng cũ. Bảng giá mới được chèn thêm (`INSERT`).
   - Lượt gửi liên kết khóa ngoại với `rate_id` tại thời điểm xe vào, đảm bảo đổi giá không làm thay đổi cước phí của xe đã vào trước đó.
4. **An toàn Chatbot Tuyệt đối (Zero SQL Execution):**
   - Vị trí: `app/services/chatbot.py`.
   - LLM (Google Gemini) chỉ đóng vai trò phân loại ý định (NLU) và diễn đạt câu trả lời (NLG).
   - Backend tự thực thi các hàm đọc CSDL cố định (hardcoded). Gemini **không bao giờ** nhận kết nối database, không được viết hay thực thi SQL.
   - Luôn có bộ Fallback bằng Regex và template nội bộ nếu Gemini mất kết nối mạng.
5. **Nguyên tắc Thị giác máy tính Human-in-the-Loop:**
   - AI chỉ đưa ra gợi ý (biển số, ô đỗ có xe/trống).
   - AI **không bao giờ tự động ghi đè CSDL nghiệp vụ**. Khi AI phát hiện mâu thuẫn, nó chỉ sinh bản ghi cảnh báo `alerts` và bật cờ `needs_review = True` để con người xác thực.

---

## 4. Các Giới Hạn và Quy Tắc Bắt Buộc Khi Làm Việc

1. **Tuyệt đối KHÔNG chạy lệnh làm thay đổi CSDL Production hoặc CSDL thật:**
   - Khi chạy test, chỉ chạy qua `pytest`. File `tests/conftest.py` đã cấu hình SQLite tạm thời biệt lập, hoàn toàn an toàn.
2. **Tuyệt đối KHÔNG tự ý sửa file `config/demo_parking/0922_1_polygons.json` hoặc DDL `schema.sql`:**
   - Tọa độ 24 đa giác đã được khớp chính xác với video demo. Sửa tọa độ sẽ làm sai lệch toàn bộ kết quả phân tích.
   - Sửa `schema.sql` mà không chạy migration đồng bộ trên SQL Server sẽ gây lỗi sập ứng dụng.
3. **Tuyệt đối KHÔNG lưu Secret/Mật khẩu vào mã nguồn:**
   - Luôn đọc qua `os.getenv()`.
   - File `app/services/common.py:audit()` nghiêm cấm lưu mật khẩu, API key hoặc connection string vào cột `detail`.
4. **Quy tắc Phân quyền (RBAC):**
   - Mọi route hoặc API liên quan đến quản trị (Rates, Users, Statistics, Chatbot, Backups, Areas, Monthly New/Renew) **bắt buộc** phải có decorator `@manager_only`.
   - Vai trò `STAFF` chỉ được phép thực hiện các thao tác vận hành hàng ngày.

---

## 5. Các Lệnh Kiểm Tra An Toàn (Safe Commands)

Khi bạn muốn kiểm tra hệ thống mà không gây tác dụng phụ:

```powershell
# 1. Chạy toàn bộ 59 test cases kiểm thử tự động (sử dụng SQLite tạm thời)
pytest

# 2. Chạy riêng kiểm thử nghiệp vụ và phân quyền
pytest tests/test_business.py

# 3. Chạy riêng kiểm thử thuật toán tính phí
pytest tests/test_billing.py

# 4. Kiểm tra tính toàn vẹn cú pháp các file Python
python -m py_compile run.py app/routes.py app/models.py app/services/parking.py

# 5. Kiểm tra kết nối SQL Server (script chẩn đoán an toàn, chỉ đọc)
python tools/diagnose_sql_connection.py
```

---

## 6. Những Thông Tin Cần Hỏi Người Dùng Trước Khi Thao Tác Có Rủi Ro

Nếu người dùng yêu cầu thực hiện các hành động sau, bạn **PHẢI DỪNG LẠI VÀ HỎI XÁC NHẬN**:
1. **Chạy Migration CSDL thực tế:** Hỏi xác nhận xem CSDL SQL Server mục tiêu đã có bản sao lưu `.bak` hợp lệ hay chưa trước khi chạy script `migrate_parking_slots_ab_to_d01_d24_draft_not_executed.sql`.
2. **Mở rộng thêm ô đỗ ngoài 24 ô:** Hỏi người dùng về quy chuẩn đặt tên ô và việc chấp thuận sửa đổi CHECK constraint `ck_fixed_slot` trong CSDL SQL Server.
3. **Thay đổi công thức tính phí:** Hỏi xác nhận về chính sách kinh doanh (khung đêm, thời gian tối thiểu, giá block) trước khi sửa `app/services/billing.py`.
4. **Xóa/Dọn dẹp dữ liệu bảng `observations` hoặc `audit_logs`:** Bắt buộc hỏi chính sách lưu trữ (retention window, ví dụ 14 ngày hay 30 ngày) trước khi thực thi lệnh purge.
