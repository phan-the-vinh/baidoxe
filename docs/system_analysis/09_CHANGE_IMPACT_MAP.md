# BẢN ĐỒ PHỤ THUỘC VÀ TÁC ĐỘNG THAY ĐỔI (CHANGE IMPACT MAP)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Mục tiêu:** Hướng dẫn kỹ sư hoặc AI tiếp nhận xác định chính xác các thành phần liên đới trước khi chỉnh sửa bất kỳ module nào, ngăn chặn lỗi dây chuyền (Regression Bugs).  

---

## 1. Ma Trận Phụ Thuộc Giữa Các Tầng (Component Dependency Graph)

```text
[Web / API Controllers (app/routes.py)]
   │
   ├──> [app/services/common.py] (write_lock, validators, RBAC, audit)
   │       └──> [app/models.py] (SiteLock, AuditLog, User)
   │
   ├──> [app/services/parking.py] (Core Workflows)
   │       ├──> [app/services/billing.py] (calculate_fee)
   │       ├──> [app/services/common.py] (write_lock, audit, normalize_plate)
   │       ├──> [app/services/parking_layout.py] (current_layout)
   │       └──> [app/models.py] (Visit, Slot, Rate, MonthlyPass, PassPeriod, Alert)
   │
   ├──> [app/services/analytics.py] (Statistics & Capacity)
   │       ├──> [app/models.py] (ParkingCapacityHistory, Visit, Payment, Slot)
   │       └──> [app/services/common.py] (VN timezone, date parsing)
   │
   ├──> [app/services/vision_video.py] (Async Video Engine)
   │       ├──> [tools/analyze_raw_parking_video.py] (External Worker Subprocess)
   │       │       ├──> [tools/parking_polygon_config.py]
   │       │       └──> [tools/yolo_polygon_logic.py]
   │       └──> [config/demo_parking/0922_1_polygons.json]
   │
   ├──> [app/services/plate_detection.py] & [app/services/plate_ocr.py]
   │       ├──> [tools/detect_car_plates.py] (Car -> Plate YOLO Worker)
   │       └──> [Tesseract OCR Binary (external)]
   │
   └──> [app/services/chatbot.py] (NLU Assistant)
           ├──> [Google GenAI SDK (gemini-1.5/2.0)]
           └──> [Direct Read-only Database Queries on SQL Server]
```

---

## 2. Phân Tích Tác Động Khi Thay Đổi Từng Thành Phần (Impact Analysis)

### 2.1. Khi Thay Đổi Cấu Trúc Bãi Xe hoặc Thêm Ô Đỗ Mới
- **Kịch bản:** Mở rộng bãi xe từ 24 ô lên 30 ô (ví dụ thêm D25–D30 hoặc khu E).
- **Các tệp bắt buộc phải đồng bộ cập nhật:**
  1. `app/models.py`: Cập nhật constraint `ck_fixed_slot` và `TARGET_SLOT_CODES`.
  2. `database/schema.sql`: Sửa DDL constraint `ck_fixed_slot` tương ứng.
  3. `app/cli.py`: Sửa danh sách seed và baseline sức chứa (`ParkingCapacityHistory`).
  4. `app/services/parking_layout.py`: Cập nhật logic `current_layout()` nhận diện layout mới.
  5. `config/demo_parking/0922_1_polygons.json`: Bổ sung tọa độ đa giác cho các ô đỗ mới.
  6. `app/services/vision_video.py`: Cập nhật `SLOT_IDS = tuple(f"D{number:02d}" for number in range(1, 31))`.
  7. `app/services/demo_ai_playback.py`: Cập nhật hằng số `SLOT_IDS`.
  8. `app/templates/slots.html`, `app/templates/entry.html`: Kiểm tra bố cục CSS hiển thị lưới ô đỗ.
- **Rủi ro nếu bỏ sót:** CSDL SQL Server sẽ ném lỗi ngoại lệ vi phạm CHECK constraint `ck_fixed_slot`, khiến API check-in thất bại.

---

### 2.2. Khi Thay Đổi Thuật Toán Tính Phí (`app/services/billing.py`)
- **Kịch bản:** Đổi khung giờ ban đêm (ví dụ từ 21:00 đến 07:00) hoặc đổi thời lượng block (ví dụ block 1 giờ thay vì 2 giờ).
- **Các tệp bắt buộc phải xem xét:**
  1. `app/services/billing.py`: Điều chỉnh logic hàm `calculate_fee()`.
  2. `docs/NGHIEP_VU.md`: Cập nhật tài liệu quy tắc nghiệp vụ và ví dụ đối soát.
  3. `tests/test_billing.py`: Cập nhật lại 14 ca kiểm thử biên (Boundary Cases). Bắt buộc chạy `pytest tests/test_billing.py` để xác minh.
  4. `app/services/parking.py:quote()`: Kiểm tra tính tương thích của dữ liệu trả về cho giao diện thu ngân.
- **Rủi ro nếu bỏ sót:** Gây lệch tiền giữa lúc xem báo giá (`quote`) và lúc chốt tiền ra (`checkout`), kích hoạt lỗi 409 Conflict liên tục cho thu ngân.

---

### 2.3. Khi Thay Đổi Trọng Số Model YOLO hoặc Thuật Toán Gán Đa Giác
- **Kịch bản:** Cập nhật file trọng số `best.pt` mới hoặc tinh chỉnh ngưỡng IoU / Hysteresis.
- **Các tệp liên đới:**
  1. `tools/yolo_polygon_logic.py`: Tinh chỉnh các ngưỡng `OVERLAP_THRESHOLD`, `TemporalSlotState`.
  2. `tools/analyze_raw_parking_video.py`: Đồng bộ tham số dòng lệnh.
  3. `app/services/vision_video.py`: Cập nhật đường dẫn `WEIGHTS_PATH`.
  4. `tests/test_yolo_polygon_logic.py`: Chạy lại toàn bộ test logic gán ô đỗ.
- **Rủi ro nếu bỏ sót:** Phát hiện sai lệch trạng thái ô đỗ (False Positive: ô trống báo có xe hoặc False Negative: có xe báo ô trống), sinh cảnh báo giả tràn ngập bảng `alerts`.

---

### 2.4. Khi Thay Đổi Authentication hoặc Cơ Chế Khóa Giao Dịch
- **Kịch bản:** Tinh chỉnh hàm `write_lock()` hoặc decorator `@manager_only`.
- **Các tệp liên đới:**
  1. `app/services/common.py`: Nơi định nghĩa `write_lock()` và `@manager_only`.
  2. `app/routes.py`: Mọi endpoint có dùng `write_lock()` (Check-in, Check-out, Rates, Areas, Users).
  3. `tests/test_business.py`: Kiểm thử quyền truy cập 403 của nhân viên và tính nguyên tử của giao dịch.
- **Rủi ro nếu bỏ sót:** Mở ra lỗ hổng vượt quyền (Privilege Escalation) cho nhân viên hoặc gây Deadlock trong CSDL SQL Server.

---

## 3. Checklist Bắt Buộc Trước Khi Commit Thay Đổi (Pre-Commit Verification)

Trước khi xác nhận bất kỳ thay đổi nào vào mã nguồn, kỹ sư bắt buộc phải hoàn thành các bước kiểm tra sau:

- [ ] **1. Kiểm tra Cú pháp & Static Type:** Đảm bảo không có lỗi cú pháp Python (`python -m py_compile <changed_file.py>`).
- [ ] **2. Chạy Bộ Kiểm Thử Đơn Vị Tự Động:**
  ```powershell
  pytest
  ```
  Xác nhận toàn bộ **59 test cases** phải vượt qua (Passed).
- [ ] **3. Kiểm tra Tính Toàn Vẹn Transaction:** Nếu sửa các hàm trong `parking.py`, phải xác nhận hàm `write_lock()` luôn được gọi trước câu truy vấn đọc dữ liệu nghiệp vụ đầu tiên trong mutation.
- [ ] **4. Kiểm tra Quyền Hạn (RBAC Check):** Mọi route mới thêm vào nếu thuộc chức năng quản trị phải được bảo vệ bằng `@manager_only`.
- [ ] **5. Kiểm tra Secret Leakage:** Tuyệt đối không hardcode mật khẩu, chuỗi kết nối hoặc API key vào mã nguồn; chỉ đọc từ `os.getenv()`.
