# BÁO CÁO TỔNG QUAN HỆ THỐNG (EXECUTIVE SUMMARY)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Đường dẫn phân tích:** `c:\Users\Admin\Downloads\He_thong_bai_do_o_to_AI_local\parking_ai`  
> **Thời điểm lập:** 2026-10-27  
> **Người lập:** Senior Software Architect & AI Engineer  

---

## 1. Mục đích Hệ thống & Bài toán giải quyết
Hệ thống **Parking AI** là giải pháp phần mềm quản trị bãi đỗ xe ô tô cục bộ (On-Premises / Local) phục vụ kiểm soát phương tiện vào/ra, tự động hóa tính cước và hỗ trợ giám sát bãi đỗ bằng thị giác máy tính và trí tuệ nhân tạo.
- **Vận hành cốt lõi:** Quản lý vòng đời lượt gửi xe (`visits`), kiểm soát xe vào (`check_in`), kiểm soát xe ra (`check_out`), xác nhận vị trí đỗ, xử lý mất vé không phụ thu, in biên nhận và thanh toán tiền mặt.
- **Tính cước lũy tiến:** Tự động tính tiền theo block 2 giờ ban ngày, áp dụng phụ thu qua đêm (khung 22:00 – 08:00, tối thiểu 5 giờ), và quản lý chu kỳ vé tháng.
- **Thị giác máy tính (AI Vision):** Giám sát trạng thái ô đỗ (trống/có xe) từ camera/video thông qua vùng quan tâm đa giác (Polygon ROI) và gợi ý biển số xe từ ảnh (License Plate OCR) theo nguyên tắc **Human-in-the-Loop** (bắt buộc nhân viên xác nhận).
- **Hỗ trợ quản trị (AI Chatbot):** Cung cấp giao diện đàm thoại tiếng Việt cho Quản lý thông qua mô hình ngôn ngữ lớn (Google Gemini), được cô lập hoàn toàn khỏi CSDL (Read-only, Zero SQL Execution, Intent Whitelist).

---

## 2. Trạng thái Thực tế của Dự án (Reality vs. Documentation)

Qua đối chiếu mã nguồn thực tế với các tài liệu (`README.md`, `KIEN_TRUC.md`, `NGHIEP_VU.md`), hệ thống đang ở **trạng thái chuyển giao công nghệ kép (Dual-State / Transitional Phase)**:

| Thành phần | Trạng thái ban đầu (Legacy theo tài liệu) | Trạng thái thực tế trong mã nguồn | Đánh giá xác minh |
|---|---|---|---|
| **Mô hình Bãi xe** | 20 ô đỗ phân bổ 2 khu: Khu A (`A01`–`A10`), Khu B (`B01`–`B10`). | Hệ thống đã được mở rộng hỗ trợ 24 ô đỗ thuộc Khu D (`D01`–`D24`). Seed mặc định CSDL mới là 24 ô Khu D. | **Đã xác minh** qua `app/models.py:L8-9`, `app/cli.py:L16-24`. |
| **Mô hình AI Ô đỗ** | Tài liệu khẳng định: *CNN Keras tự train from-scratch 96x96, không dùng YOLO/ResNet/MobileNet*. | Thực tế tồn tại 2 luồng:<br>1. Luồng cũ (`ai/`): Keras CNN 20 ô (bị vô hiệu hóa tự động nếu bãi là D01–D24).<br>2. Luồng mới (`app/services/vision_video.py`): Pipeline **YOLOv8n + Polygon Logic** chạy trên video bãi xe D01–D24. | **Đã xác minh** qua `app/services/vision.py:L40-45`, `app/services/vision_video.py:L30-35`. |
| **Nhận diện Biển số** | Tài liệu mô tả: *OpenCV + Tesseract gợi ý biển số*. | Thực tế đã xây dựng thêm pipeline 2 giai đoạn: **Car Detector YOLO + Plate Detector YOLO** (`app/services/plate_detection.py`) kết hợp Tesseract OCR. | **Đã xác minh** qua `app/services/plate_detection.py:L20-22`. |
| **Dữ liệu Video Demo** | Tài liệu ghi nhận không có dataset thật. | Thực tế đã có gói artifact xử lý trước đầy đủ: video demo `yolo_polygon_annotated_h264.mp4` (412 MB) và timeline trạng thái 24 ô D01–D24. | **Đã xác minh** qua `app/services/demo_ai_playback.py:L12-23`. |

---

## 3. Tóm tắt Kiến trúc Hệ thống

- **Kiến trúc chính:** **Modular Monolith** viết bằng Python 3.11 trên nền tảng Flask (Application Factory Pattern).
- **Web & Application Server:** Waitress WSGI Server (`threads=6`), chạy local bound tại `127.0.0.1:5000`.
- **Giao diện:** Jinja2 Server-Side Rendering kết hợp Vanilla JavaScript (ES6+), Bootstrap 5.3.3 và Chart.js 4.4.7 (lưu cục bộ trong `app/static/vendor`, không dùng CDN).
- **Cơ sở dữ liệu:** Microsoft SQL Server (Transact-SQL) kết nối qua SQLAlchemy 2.0 và PyODBC (ODBC Driver 18 for SQL Server).
- **Cơ chế đồng thời (Concurrency):** Khóa bi quan toàn cục mức bảng (`site_lock WITH (UPDLOCK, HOLDLOCK)`) đảm bảo tuần tự hóa tuyệt đối mọi thao tác ghi, chống Double-Entry và Overbooking.
- **Trí tuệ nhân tạo (In-Process & External):**
  - AI Ô đỗ: YOLOv8n (`runs/detect/runs/yolo_car_v12/.../best.pt`) & Keras CNN (`ai/`).
  - OCR Biển số: YOLO Car/Plate Detector + Tesseract OCR (`pytesseract`).
  - Trợ lý thông minh: Google Gemini API (NLU intent extraction + Natural Language generation) với fallback nội bộ 100%.

---

## 4. Những Vấn đề và Rủi ro Quan trọng Nhất

1. **[High] Điểm thắt nút chai Coarse-Grained Lock (`site_lock`):** Mọi thao tác ghi (xe vào, xe ra, đổi giá, đổi vị trí) đều chờ trên 1 dòng duy nhất của bảng `site_lock`. Thiết kế này an toàn cho bãi 20–24 xe nhưng sẽ gây tắc nghẽn nghiêm trọng nếu mở rộng sang nhiều cổng/làn xe.
2. **[High] Cố định cứng số lượng ô đỗ ở tầng DDL Schema:** Ràng buộc `ck_fixed_slot` và `ck_fixed_area` giới hạn cứng tên các ô (A01–A10, B01–B10, D01–D24). Muốn thêm ô đỗ mới bắt buộc phải thay đổi DDL constraint của SQL Server.
3. **[Medium-High] Rủi ro cạn kiệt tài nguyên Worker do tác vụ AI đồng bộ:** Tác vụ Tesseract OCR và xử lý video chạy trực tiếp in-process có thể làm nghẽn 6 worker threads của Waitress, ảnh hưởng đến thời gian phản hồi giao diện kiểm soát xe vào/ra.
4. **[Medium] Tăng trưởng dữ liệu không giới hạn:** Bảng `observations` ghi nhận từng lượt quan sát AI chưa có cơ chế dọn dẹp định kỳ (Retention/Purge Policy).

---

## 5. Giới hạn Xác minh Kỹ thuật
- **Môi trường SQL Server thực tế:** Đánh giá dựa trên mã nguồn, cấu hình và schema SQL trong repository (`database/schema.sql`). Trong môi trường phân tích hiện tại, cơ sở dữ liệu production chưa được kết nối trực tiếp; toàn bộ test tự động sử dụng SQLite test engine (`tests/conftest.py`).
- **Phần cứng GPU/Camera:** Không có thiết bị camera RTSP vật lý hay GPU kết nối trực tiếp trong môi trường phân tích; các tính năng video được xác minh qua các test file và video mẫu đã ghi nhận.
