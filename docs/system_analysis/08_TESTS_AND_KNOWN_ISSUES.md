# BỘ KIỂM THỬ VÀ CÁC VẤN ĐỀ TỒN ĐỌNG (TESTS AND KNOWN ISSUES)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Nguồn đối chiếu:** `tests/`, `docs/pytest_result.txt`, `docs/KIEM_THU.md`  
> **Trạng thái:** Đã xác minh qua mã nguồn và kết quả kiểm thử tự động  

---

## 1. Khảo Sát Bộ Kiểm Thử Tự Động (Test Suite)

Dự án sở hữu một bộ kiểm thử tự động toàn diện viết bằng `pytest` gồm **12 tệp kiểm thử**, bao phủ toàn bộ các use cases nghiệp vụ từ UC001 đến UC020:

| Tệp kiểm thử (`tests/`) | Mục tiêu kiểm thử | Các ca kiểm thử chính |
|---|---|---|
| `conftest.py` | Cấu hình Fixture kiểm thử cô lập | Sử dụng SQLite tạm (`tmp_path / 'test.db'`), seed tài khoản `manager` và `employee`. Đảm bảo **không bao giờ ảnh hưởng tới CSDL SQL Server thật**. |
| `test_billing.py` | Tính toán cước phí lũy tiến đa mốc biên | Kiểm tra 14 mốc biên thời gian (block 2h, đủ 5h đêm, thiếu 1 giây đêm, gửi nhiều đêm, xe vào ra cùng lúc, vé tháng miễn phí). |
| `test_business.py` | Luồng nghiệp vụ bãi xe & RBAC | Kiểm tra nguyên tử tính Check-in (chống trùng biển số, chống trùng ô), Checkout chính xác 1 lần, đối soát tiền mặt, đổi giá snapshot, xử lý mất vé, chặn quyền STAFF truy cập API Quản lý. |
| `test_chatbot.py` | Trợ lý Chatbot NLU & Fallback | 11 câu hỏi tự nhiên tiếng Việt, kiểm tra kích hoạt Fallback khi mất mạng/lỗi Gemini, kiểm tra bắt lỗi mã ô ngoài phạm vi D01–D24. |
| `test_plate_ocr.py` | Chuẩn hóa và gợi ý biển số | Kiểm tra regex biển số Việt Nam, xử lý dấu chấm/gạch và kiểm tra tính an toàn chỉ gợi ý. |
| `test_plate_detection.py` | Car & Plate Detector YOLO | Kiểm tra trạng thái sẵn sàng của 2 mô hình YOLO phát hiện xe và biển số. |
| `test_parking_polygon_config.py` | Tải và co dãn tọa độ đa giác | Kiểm tra đọc file `0922_1_polygons.json`, scale điểm theo kích thước ảnh thực tế. |
| `test_polygon_editor_coordinates.py` | Chuyển đổi tọa độ Editor | Kiểm tra thuật toán biến đổi affine/perspective trong công cụ vẽ polygon. |
| `test_yolo_polygon_logic.py` | Thuật toán gán xe vào ô đỗ | Kiểm tra gán độc quyền 1 xe/1 ô, lọc vùng loại trừ `X01`, hộp lõi xe (`vehicle_core_box`), bộ lọc thời gian chống nhấp nháy (`TemporalSlotState`). |
| `test_vision_video.py` | Vòng đời Job phân tích video | Kiểm tra xác thực quyền truy cập video, khởi tạo job, kiểm tra tiến độ và sinh timeline. |
| `test_ai_demo.py` | Phát lại dữ liệu demo D24 | Kiểm tra đọc các tệp CSV trạng thái demo, phát hiện lỗi dữ liệu không đồng nhất. |
| `test_ai_data.py` | Tính toàn vẹn dataset & băm SHA-256 | Kiểm tra hash kiểm định model và kiểm tra trùng lặp ảnh giữa tập train và test. |

- **Kết quả kiểm thử đã ghi nhận (`docs/pytest_result.txt`):**
  - **59 passed in 14.48s** (100% test cases thành công, độ bao phủ logic toàn diện).

---

## 2. Bảng Phân Loại Các Vấn Đề và Rủi Ro Kỹ Thuật (Known Issues & Risk Matrix)

Các vấn đề được phân loại theo mức độ nghiêm trọng: **Critical / High / Medium / Low / Informational**, kèm bằng chứng mã nguồn cụ thể.

| Mức độ | Vấn đề phát hiện | Bằng chứng mã nguồn | Kịch bản tác động | Biện pháp khắc phục đề xuất |
|:---:|---|---|---|---|
| **High** | **Nút thắt cổ chai Coarse-Grained Locking (`site_lock`)** | `app/services/common.py:L68-82` (`SELECT SiteLock WITH (UPDLOCK, HOLDLOCK)`) | Mọi thao tác ghi (xe vào, xe ra, đổi ô, đổi giá) đều khóa toàn bộ bãi xe. Khi nhiều xe vào/ra đồng thời tại các cổng khác nhau, các giao dịch sẽ bị nghẽn hàng đợi (serialization bottleneck). | Chuyển sang cơ chế **Row-level Locking** (khóa trên từng dòng của ô đỗ `Slot` hoặc lượt gửi `Visit` tương ứng) hoặc dùng Optimistic Concurrency Control với cột version. |
| **High** | **Cố định cứng mã ô đỗ ở DDL Constraint** | `app/models.py:L58-64`, `database/schema.sql:L143` (`CONSTRAINT ck_fixed_slot`) | Schema CSDL chỉ chấp nhận các mã ô liệt kê sẵn (`A01–A10`, `B01–B10`, `D01–D24`). Nếu bãi xe mở rộng thêm ô mới (ví dụ D25, E01), hệ thống sẽ ném lỗi SQL Server và dừng hoạt động. | Xóa bỏ constraint `ck_fixed_slot`, chuyển quyền kiểm soát tính hợp lệ của ô đỗ sang quan hệ cha-con linh hoạt giữa bảng `areas` và `slots`. |
| **Medium-High** | **Xung đột tài nguyên Worker do tác vụ AI đồng bộ** | `app/services/plate_ocr.py:L26` (`_ocr_gate = BoundedSemaphore(1)`), `app/services/vision.py:L21` | Tesseract OCR và Keras inference tốn từ 200–800ms CPU. Do Waitress chỉ có 6 worker threads, các request OCR dồn dập sẽ chiếm giữ thread, làm đơ các thao tác check-in/check-out của thu ngân. | Tách việc xử lý OCR/AI ra khỏi vòng đời HTTP Request bằng hàng đợi tác vụ nền (Background Queue / Worker Process riêng biệt). |
| **Medium** | **Bảng `observations` phình to không giới hạn** | `app/models.py:L147-156`, `database/schema.sql:L171-182` | Mỗi lần camera phân tích 24 ô, 24 bản ghi mới được chèn vào `observations`. Sau vài tuần hoạt động, bảng này sẽ đạt hàng triệu dòng mà không có chính sách xóa/lưu trữ cũ (Retention Policy). | Xây dựng Scheduled Job định kỳ dọn dẹp (truncate hoặc archive) các bản ghi `observations` cũ hơn 14 ngày không liên quan tới `alerts`. |
| **Medium** | **Quản lý trạng thái Job và Rate-limit trong RAM** | `app/services/vision_video.py:L42` (`_jobs: dict`), `app/routes.py:L19` (`_attempts: OrderedDict`) | Toàn bộ trạng thái tiến độ video và số lần thử sai mật khẩu lưu trong biến toàn cục Python. Nếu khởi động lại tiến trình server, toàn bộ trạng thái job đang chạy và lịch sử rate-limit sẽ bị mất sạch. | Lưu trạng thái Job phân tích video vào CSDL hoặc Redis. |
| **Low** | **Giao thức HTTP không mã hóa mặc định** | `run.py:L9-10` (`serve(application, host='127.0.0.1', port=5000)`) | Lưu lượng truyền trong mạng nội bộ nếu mở cổng LAN có nguy cơ bị nghe lén (Sniffing) mật khẩu hoặc dữ liệu biển số. | Triển khai Nginx Reverse Proxy phía trước với chứng chỉ SSL/TLS (HTTPS). |
| **Informational** | **Trạng thái chuyển tiếp kiến trúc kép (Dual-Architecture)** | `app/services/parking_layout.py:L7-36`, `app/services/vision.py:L40-45` | Mã nguồn tồn tại song song 2 luồng: CNN 20 ô cũ (A/B) và YOLO 24 ô mới (D). Kỹ sư mới đọc tài liệu sẽ dễ nhầm lẫn chức năng nào đang thực sự chạy. | Đọc kỹ bộ tài liệu bàn giao `docs/system_analysis/` để nắm rõ luồng active hiện tại. |
