# CẤU TRÚC DỰ ÁN VÀ PHÂN BỔ TÀI NGUYÊN (PROJECT STRUCTURE)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Trạng thái:** Đã xác minh qua khảo sát filesystem thực tế  

---

## 1. Sơ đồ Cây Thư mục Thực tế

```text
parking_ai/
│
├── .env.example                          # Mẫu cấu hình biến môi trường
├── .env                                  # Cấu hình môi trường local (chứa connection string, key)
├── requirements.txt                      # Dependencies cơ bản cho Flask, SQLAlchemy, OpenCV, PyODBC, Gemini
├── requirements-ai.txt                   # Dependencies mở rộng cho AI: TensorFlow, Scikit-learn, Matplotlib
├── requirements-dev.txt                  # Dependencies cho môi trường phát triển & kiểm thử: Pytest
├── pytest.ini                            # Cấu hình test runner pytest
├── run.py                                # Entry point khởi chạy web server cục bộ (Waitress)
├── yolov8n.pt                            # Trọng số gốc YOLOv8n (COCO pretrained)
│
├── app/                                  # Mã nguồn ứng dụng web chính (Flask Monolith)
│   ├── __init__.py                       # Application Factory (create_app), cấu hình filter, error handlers
│   ├── cli.py                            # CLI commands (flask init-db, seed_database)
│   ├── config.py                         # Load biến môi trường, tạo SQLAlchemy ODBC URL cho SQL Server
│   ├── extensions.py                     # Khởi tạo db (SQLAlchemy), login_manager (Flask-Login), csrf
│   ├── models.py                         # 12 SQLAlchemy Models & DDL Constraints, Indexes
│   ├── routes.py                         # HTTP controllers (Blueprints: 'web' và 'api')
│   ├── services/                         # Business & Service Layer (Tách bạch logic khỏi HTTP routes)
│   │   ├── analytics.py                  # Thống kê doanh thu, lưu lượng, tỷ lệ lấp đầy, sức chứa lịch sử
│   │   ├── backup.py                     # Sao lưu CSDL SQL Server native (.bak) kèm VERIFYONLY
│   │   ├── billing.py                    # Thuật toán tính phí lũy tiến block 2h và khung đêm
│   │   ├── chatbot.py                    # NLU Intent Whitelist, gọi Gemini và Fallback Engine
│   │   ├── common.py                     # write_lock(), normalize_plate(), audit(), validators
│   │   ├── demo_ai_playback.py           # Đọc artifact video demo YOLO + timeline trạng thái D24
│   │   ├── parking.py                    # Nghiệp vụ: check_in, check_out, quote, move_visit, monthly
│   │   ├── parking_layout.py             # Nhận diện layout CSDL: LEGACY_AB20 vs D24
│   │   ├── plate_detection.py            # Bridge nhận diện xe & biển số 2 giai đoạn (YOLO Car + Plate)
│   │   ├── plate_ocr.py                  # OpenCV tiền xử lý & Tesseract OCR gợi ý biển số
│   │   ├── vision.py                     # Giám sát ô đỗ bằng ảnh chụp đơn / batch frame
│   │   └── vision_video.py               # Asynchronous Video Job Manager phân tích video bãi xe
│   ├── static/                           # Tài nguyên tĩnh
│   │   ├── css/style.css                 # CSS tùy biến giao diện
│   │   ├── js/app.js                     # Logic JavaScript client (fetch API, camera stream, UI alerts)
│   │   └── vendor/                       # Thư viện bên ngoài được lưu local (Bootstrap 5.3.3, Chart.js 4.4.7)
│   └── templates/                        # Giao diện HTML Jinja2 (dashboard, entry, exit, slots, chatbot, ...)
│
├── ai/                                   # Module AI Huấn luyện & Inference CNN from-scratch (Keras)
│   ├── __init__.py
│   ├── data.py                           # Hash SHA-256, kiểm tra tính toàn vẹn dataset
│   ├── evaluate_model.py                 # Đánh giá metric model phân loại ô đỗ
│   ├── model.py                          # Định nghĩa kiến trúc CNN Keras 3 khối Conv2D (96x96x3)
│   ├── predict_parking.py                # Inference độc lập từ ảnh và polygon ROI
│   ├── roi.py                            # Tải polygon ROI, cắt ảnh góc nghiêng, vẽ bounding boxes
│   ├── train_parking_model.py            # Huấn luyện CNN from-scratch với Data Augmentation
│   └── results/                          # Chứa kết quả đánh giá, ma trận nhầm lẫn (confusion matrix)
│
├── config/                               # Tệp cấu hình bãi xe và polygon ROI
│   ├── parking_slots.json                # Cấu hình 20 ROI cũ (Khu A: A01-A10, Khu B: B01-B10)
│   └── demo_parking/                     # Cấu hình bãi xe demo mới (Khu D: 24 ô D01-D24)
│       ├── 0922_1_polygons.json          # Tọa độ 24 đa giác ô đỗ D01-D24 đã căn chỉnh
│       └── 0922_1_reference_t05s.jpg     # Khung hình mẫu đối chiếu tại giây thứ 5
│
├── database/                             # Script CSDL SQL Server và kế hoạch chuyển đổi
│   ├── create_database.sql               # Tạo CSDL ParkingAI trên SQL Server
│   ├── schema.sql                        # DDL Schema hoàn chỉnh (bảng, khóa, indexes, constraints)
│   ├── migrate_parking_slots_ab_to_d01_d24_draft_not_executed.sql # Script chuyển đổi A/B sang D01-D24
│   └── migrate_parking_slots_ab_to_d01_d24_test_plan.md           # Kế hoạch và checklist kiểm thử migration
│
├── dataset/                              # Thư mục lưu trữ và hướng dẫn chuẩn bị dữ liệu
│   ├── README.md                         # Hướng dẫn thu thập, gán nhãn và chia tập train/val/test
│   ├── parking/                          # Cấu trúc rỗng theo chuẩn train/validation/test
│   └── [raw & benchmark folders]         # Thư mục raw biển số và nhãn YOLO
│
├── docs/                                 # Tài liệu kỹ thuật, đối chiếu và kiểm thử
│   ├── DOI_CHIEU_UC.md                   # Đối chiếu 20 Use Cases (UC001–UC020) với mã nguồn
│   ├── KIEN_TRUC.md                      # Kiến trúc tổng thể và cơ chế giao dịch
│   ├── NGHIEP_VU.md                      # Quy tắc nghiệp vụ chi tiết và ví dụ tính phí
│   ├── THIRD_PARTY.md                    # Bản quyền và giấy phép mã nguồn mở bên thứ ba
│   ├── pytest_result.txt                 # Kết quả thực thi kiểm thử tự động (59 passed)
│   └── system_analysis/                  # BỘ TÀI LIỆU BÀN GIAO KỸ THUẬT (MỚI TẠO)
│
├── runs/                                 # Kết quả huấn luyện và thử nghiệm YOLO thực tế
│   └── detect/
│       ├── runs/yolo_car_v12/            # Model phát hiện xe hơi (train_27_from_smoke01_best/weights/best.pt)
│       ├── runs/license_plate/           # Model phát hiện biển số xe (train_30/weights/best.pt)
│       └── polygon_tests/yolo_car_v12_best/full_video_d16_logic_fix_conf040/ # Video demo và CSV states D24
│
├── tests/                                # Bộ kiểm thử tự động Pytest (59 test cases)
│   ├── conftest.py                       # Fixture tạo app context, SQLite test DB và seed dữ liệu
│   ├── test_billing.py                   # Kiểm thử thuật toán tính phí tại các mốc biên thời gian
│   ├── test_business.py                  # Kiểm thử toàn bộ nghiệp vụ xe vào/ra, thanh toán, phân quyền
│   ├── test_chatbot.py                   # Kiểm thử Intent routing, fallback và ngăn chặn SQL
│   ├── test_parking_polygon_config.py    # Kiểm thử parse và scale tọa độ polygon
│   ├── test_plate_detection.py           # Kiểm thử bridge nhận diện biển số
│   ├── test_plate_ocr.py                 # Kiểm thử OCR regex và chuẩn hóa
│   ├── test_vision_video.py              # Kiểm thử vòng đời phân tích video
│   └── test_yolo_polygon_logic.py        # Kiểm thử gán xe vào ô đỗ và temporal smoothing
│
├── tools/                                # 45 scripts công cụ quản trị, gán nhãn, chẩn đoán và migrate
│   ├── analyze_raw_parking_video.py      # Worker phân tích video bãi xe với YOLO + Polygon
│   ├── annotate_demo_parking_polygons.py # Công cụ vẽ và căn chỉnh đa giác ô đỗ
│   ├── detect_car_plates.py              # Worker nhận diện xe và cắt bounding box biển số
│   ├── parking_roi_editor.py             # Giao diện OpenCV vẽ polygon ROI thủ công
│   ├── setup_env.py                      # Wizard tương tác khởi tạo file .env
│   ├── yolo_polygon_logic.py             # Thuật toán cốt lõi tính IoU đa giác và trạng thái thời gian
│   └── [chẩn đoán & verify scripts]      # Các script kiểm tra SQL, HTTP, OCR, Chatbot
│
└── weights/                              # Trọng số bổ trợ
    └── yolo26n.pt                        # Trọng số YOLO bổ trợ
```

---

## 2. Phân định Tài nguyên Đang Sử dụng vs. Tài nguyên Chuyển đổi / Cũ

Một kỹ sư mới tiếp nhận dự án rất dễ nhầm lẫn nếu chỉ đọc tài liệu gốc. Dưới đây là phân định chính xác dựa trên mã nguồn thực tế:

| Phân hệ | Tài nguyên Cũ / Di sản (Legacy / Transitional) | Tài nguyên Đang Sử dụng Thực tế (Active / Primary) | Bằng chứng mã nguồn |
|---|---|---|---|
| **Cấu hình Bãi xe** | `config/parking_slots.json` (20 ô A01–B10) | `config/demo_parking/0922_1_polygons.json` (24 ô D01–D24) | `app/services/vision_video.py:L34`, `app/cli.py:L16-24` |
| **Phát hiện Chiếm chỗ Ô đỗ** | `ai/model.py` (Keras CNN 96x96 train from-scratch) | `runs/detect/.../train_27_.../best.pt` kết hợp `tools/yolo_polygon_logic.py` | `app/services/vision_video.py:L30-35`, `app/services/vision.py:L40-45` (CNN bị disable trên D24) |
| **Phát hiện Biển số** | OpenCV Haar/Contour filtering đơn thuần | 2-Stage YOLO: Car Detector (`train_27`) -> Plate Detector (`train_30`) -> Tesseract OCR | `app/services/plate_detection.py:L20-22` |
| **Trình diễn Video AI** | N/A (Tài liệu nói không có video) | Artifact đã sinh tại `runs/detect/polygon_tests/.../full_video_d16_logic_fix_conf040` phát qua route `/ai-demo` | `app/services/demo_ai_playback.py:L12-23`, `app/routes.py:L215-260` |
| **CSDL Database Seed** | CSDL cũ 2 khu A/B (`areas` code 'A', 'B') | CSDL mới 1 khu D (`areas` code 'D', 24 ô `D01`–`D24`) | `app/cli.py:L16-24` |

---

## 3. Các Điểm Khởi chạy (Entry Points)

1. **Khởi chạy Ứng dụng Web Chính:**
   - File: `run.py`
   - Phương thức: `serve(application, host='127.0.0.1', port=port, threads=6)`
   - Server: Waitress WSGI.
2. **Khởi tạo và Seed Cơ sở Dữ liệu:**
   - Lệnh CLI: `flask --app run.py init-db`
   - File: `app/cli.py:init_db()`
   - Đọc cấu hình từ `.env` và seed bảng giá, tài khoản Quản lý, khu D và 24 ô đỗ.
3. **Công cụ Khởi tạo Môi trường (.env Wizard):**
   - Lệnh: `python tools/setup_env.py`
   - Tự động sinh `SECRET_KEY` ngẫu nhiên và hướng dẫn cấu hình SQL Server.
4. **Worker Phân tích Video Bãi xe Độc lập:**
   - File: `tools/analyze_raw_parking_video.py`
   - Đầu vào: `--video`, `--model`, `--polygons`.
5. **Bộ Kiểm thử Tự động:**
   - Lệnh: `pytest`
   - File cấu hình: `pytest.ini`, `tests/conftest.py`.
