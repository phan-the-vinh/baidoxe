# KIẾN TRÚC VÀ CÁC LUỒNG DỮ LIỆU (ARCHITECTURE AND FLOWS)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Mô hình kiến trúc:** Modular Monolith (Layered Architecture)  
> **Trạng thái:** Đã xác minh qua mã nguồn thực tế  

---

## 1. Sơ đồ Kiến trúc Phân tầng Tổng thể

Hệ thống được tổ chức theo 4 tầng chính: Presentation (Trình diễn), Application & Service (Nghiệp vụ), AI Engine (Thị giác máy tính) và Persistence (Lưu trữ dữ liệu).

```mermaid
graph TB
    subgraph Presentation_Layer ["Presentation Layer (Browser Client)"]
        UI["Web UI: HTML5, Bootstrap 5.3, Jinja2"]
        JS_App["app.js: Fetch API, Event Handling, CSRF Token"]
        ChartJS["Chart.js: Thống kê & Biểu đồ Lưu lượng/Doanh thu"]
        CamCapture["Camera Capture: HTML5 Canvas / MediaStream"]
    end

    subgraph App_Core ["Application Core (Flask WSGI / Waitress - 6 Threads)"]
        AppFactory["app/__init__.py: create_app()"]
        AuthModule["Flask-Login & Session Security"]
        
        subgraph Controllers ["Controllers (routes.py)"]
            WebBP["web Blueprint (Server-Side Rendering)"]
            ApiBP["api Blueprint (/api/* JSON Endpoints)"]
        end

        subgraph Service_Layer ["Service Layer (app/services/)"]
            ParkSvc["parking.py: Check-in, Check-out, Move, Monthly"]
            BillSvc["billing.py: Fee Calculation, Night Block Engine"]
            LayoutSvc["parking_layout.py: Layout Inspector (AB20 vs D24)"]
            ChatSvc["chatbot.py: Intent Router, LLM Sanitizer, Fallback"]
            BackupSvc["backup.py: MS SQL Backup (.bak) & VERIFYONLY"]
            AnalyticsSvc["analytics.py: Historical Capacity, Occupancy Math"]
            CommonSvc["common.py: write_lock(), validators, audit()"]
        end
    end

    subgraph AI_Subsystem ["AI & Computer Vision Engines"]
        subgraph Legacy_CNN ["CNN Module (ai/)"]
            KerasModel["parking_cnn_from_scratch (Keras)"]
            ROICrop["Polygon Crop & Warp (OpenCV)"]
        end

        subgraph Active_YOLO ["Active YOLO Pipeline (app/services/ & tools/)"]
            CarPlateYOLO["Car + Plate Detector YOLO (train_27 + train_30)"]
            YOLOVideoWorker["analyze_raw_parking_video.py (YOLOv8n + Polygons)"]
            TesseractOCR["Tesseract OCR Engine (pytesseract)"]
        end
    end

    subgraph Data_Storage ["Data Persistence Layer"]
        SQLServer[("Microsoft SQL Server (ParkingAI)
        - site_lock: Coarse-grained Pessimistic Lock
        - Filtered Unique Indexes (uq_active_plate, etc.)")]
        LocalDiskWrites[("Local Storage
        - Backups: .bak files
        - Uploads: parking_analysis_uploads/
        - Results: parking_analysis_results/")]
    end

    subgraph External_Cloud ["External Cloud Services"]
        GeminiAPI["Google Gemini API (NLU & NLG Context)"]
    end

    %% Client -> App
    UI --> JS_App
    CamCapture --> JS_App
    JS_App -->|HTTP POST JSON/Multipart with CSRF| ApiBP
    UI -->|HTTP GET Page Views| WebBP

    %% App Core Routing
    WebBP --> Service_Layer
    ApiBP --> Service_Layer
    AppFactory --> AuthModule
    AppFactory --> WebBP
    AppFactory --> ApiBP

    %% Service -> AI Subsystem
    ParkSvc --> CommonSvc
    ParkSvc --> BillSvc
    ParkSvc --> LayoutSvc
    ApiBP -->|POST /api/vision/parking| Legacy_CNN
    ApiBP -->|POST /api/vision/plate| Active_YOLO
    ApiBP -->|POST /api/vision/video| YOLOVideoWorker

    %% Service -> External & DB
    ChatSvc -->|Prompt with Verified Data Whitelist| GeminiAPI
    BackupSvc -->|EXEC BACKUP / RESTORE VERIFYONLY| SQLServer
    BackupSvc -->|Write Binary .bak| LocalDiskWrites
    ParkSvc -->|SELECT site_lock WITH (UPDLOCK, HOLDLOCK)| SQLServer
    AnalyticsSvc -->|Read-only Queries| SQLServer
```

---

## 2. Các Luồng Xử lý Dữ liệu Chính (Detailed Data Flows)

### 2.1. Luồng Xe Vào Bãi (Check-in Flow)
- **Mục đích:** Ghi nhận xe vào bãi an toàn, gán ô đỗ, áp dụng bảng giá hiện hành, ghi nhật ký kiểm toán.
- **Tính chất:** Đồng bộ (Synchronous), Tuần tự hóa giao dịch bằng Khóa bi quan (Pessimistic Lock).

```mermaid
sequenceDiagram
    autonumber
    actor Staff as Nhân viên vận hành
    participant Browser as Trình duyệt (app.js)
    participant Route as routes.py: api_entry()
    participant ParkSvc as parking.py: check_in()
    participant Common as common.py: write_lock()
    participant DB as SQL Server (ParkingAI)

    Staff->>Browser: Nhập biển số, chọn ô đỗ, tick xác nhận
    Browser->>Route: POST /api/visits (plate, slot_code, confirmations)
    Route->>ParkSvc: check_in(data, actor=current_user.id)
    
    rect rgb(240, 248, 255)
        note right of ParkSvc: Bắt đầu Transaction ghi dữ liệu
        ParkSvc->>Common: write_lock()
        Common->>DB: SELECT SiteLock WHERE id=1 WITH (UPDLOCK, HOLDLOCK)
        DB-->>Common: Khóa được xác lập, revision += 1
        
        ParkSvc->>DB: Kiểm tra uq_active_plate: Visit WHERE plate=plate AND exited_at IS NULL
        alt Biển số đã có lượt đang gửi
            ParkSvc-->>Route: Raise BusinessError(409, "Xe đã có lượt Đang gửi")
            Route-->>Browser: Trả về HTTP 409
        end

        ParkSvc->>DB: Kiểm tra ô đỗ: Slot.state == 'EMPTY' AND needs_review == False
        alt Ô đã có xe hoặc đang cảnh báo
            ParkSvc-->>Route: Raise BusinessError(409, "Ô không còn trống hoặc đang cần kiểm tra")
            Route-->>Browser: Trả về HTTP 409
        end

        ParkSvc->>DB: Truy vấn vé tháng: MonthlyPass + PassPeriod còn hiệu lực
        ParkSvc->>DB: Lấy bảng giá mới nhất: Rate ORDER BY id DESC LIMIT 1
        
        ParkSvc->>DB: INSERT INTO visits (ticket_code, plate, slot_code, entered_at, ticket_type, rate_id, ...)
        ParkSvc->>DB: UPDATE slots SET state='OCCUPIED', changed_at=now WHERE code=slot_code
        ParkSvc->>DB: INSERT INTO audit_logs (action='CHECK_IN', ...)
        ParkSvc->>DB: COMMIT Transaction
    end

    Route-->>Browser: HTTP 200 JSON {message: "Đã ghi nhận xe vào.", visit_id: 123}
    Browser-->>Staff: Chuyển hướng tới trang chi tiết lượt gửi / in phiếu
```

---

### 2.2. Luồng Xe Ra Bãi & Tính Cước (Check-out & Payment Flow)
- **Mục đích:** Tính toán cước phí lũy tiến chính xác theo thời gian thực tế, xác nhận thu tiền mặt và giải phóng ô đỗ.
- **Tính an toàn:** Chống chênh lệch giá (Stale Quote Protection): nếu thời gian xe ra thực tế làm thay đổi số tiền so với lúc xem báo giá (`quote`), giao diện sẽ buộc tải lại báo giá mới.

```mermaid
sequenceDiagram
    autonumber
    actor Staff as Nhân viên vận hành
    participant Browser as Trình duyệt (exit.html)
    participant Route as routes.py
    participant ParkSvc as parking.py
    participant BillSvc as billing.py
    participant DB as SQL Server (ParkingAI)

    Staff->>Browser: Tìm kiếm xe theo biển số / mã vé
    Browser->>Route: GET /api/visits/{id}/quote
    Route->>ParkSvc: quote(visit)
    ParkSvc->>BillSvc: calculate_fee(entered_at, now, rate, is_monthly)
    BillSvc-->>ParkSvc: Trả về cấu trúc phí chi tiết {total, turn_fee, night_fee, breakdown}
    ParkSvc-->>Browser: Trả về JSON {fees: {total: 40000, ...}}
    Browser-->>Staff: Hiển thị số tiền phải thu và nút xác nhận đã thu tiền

    Staff->>Browser: Bấm "Xác nhận thu tiền & Cho xe ra"
    Browser->>Route: POST /api/visits/{id}/checkout (quoted_amount=40000, confirmations)
    Route->>ParkSvc: check_out(visit_id, data, actor)
    
    rect rgb(255, 245, 238)
        note right of ParkSvc: Transaction kết thúc lượt gửi
        ParkSvc->>DB: write_lock() (UPDLOCK, HOLDLOCK trên site_lock)
        ParkSvc->>BillSvc: calculate_fee(entered_at, now, rate, is_monthly)
        alt Tiền tính lại lệch với quoted_amount
            ParkSvc-->>Route: Raise BusinessError(409, "Số tiền tính lại khác với số tiền đã xem")
            Route-->>Browser: HTTP 409 Conflict (Yêu cầu làm mới báo giá)
        end

        ParkSvc->>DB: UPDATE visits SET exited_at=now, amount=total, fee_breakdown=json, exited_by=actor
        ParkSvc->>DB: INSERT INTO payments (visit_id, amount, kind='TURN', cashier_id=actor)
        ParkSvc->>DB: UPDATE slots SET state='EMPTY', changed_at=now WHERE code=slot_code
        ParkSvc->>DB: INSERT INTO audit_logs (action='CHECK_OUT', ...)
        ParkSvc->>DB: COMMIT Transaction
    end

    Route-->>Browser: HTTP 200 JSON {message: "Đã hoàn tất xe ra."}
    Browser-->>Staff: Thông báo thành công & in biên lai thanh toán
```

---

### 2.3. Luồng Phân tích Video Bãi xe Bất đồng bộ (Async Video Analysis Flow)
- **Mục đích:** Xử lý các video bãi xe dài với model YOLOv8n mà không làm đơ hoặc timeout HTTP connection.
- **Kiến trúc:** Background Worker Thread + Polling qua REST API.

```mermaid
sequenceDiagram
    autonumber
    actor User as Quản lý / Nhân viên
    participant Browser as Trình duyệt (vision.html)
    participant Route as routes.py: api_vision_video_create()
    participant VideoSvc as vision_video.py
    participant Worker as tools/analyze_raw_parking_video.py
    participant Disk as File System (instance/parking_analysis_*)

    User->>Browser: Tải lên tệp video bãi đỗ (.mp4, max 250MB)
    Browser->>Route: POST /api/vision/video (multipart file)
    Route->>VideoSvc: create_job(upload, user_id)
    VideoSvc->>Disk: Lưu video vào instance/parking_analysis_uploads/{job_id}.mp4
    VideoSvc->>VideoSvc: Khởi chạy Python Thread chạy Worker ngầm
    VideoSvc-->>Route: Trả về Job Metadata {id, status: 'QUEUED'}
    Route-->>Browser: HTTP 202 Accepted {job_id: "..."}

    par Tiến trình nền (Background Execution)
        VideoSvc->>Worker: subprocess.run([python, analyze_raw_parking_video.py, ...])
        Worker->>Worker: Chạy YOLOv8n inference trên từng frame
        Worker->>Worker: Gán detection vào 24 polygon D01-D24 qua yolo_polygon_logic.py
        Worker->>Disk: Ghi kết quả vào instance/parking_analysis_results/{job_id}/timeline.json
        VideoSvc->>VideoSvc: Cập nhật Job status = 'FINISHED'
    and Polling trên Trình duyệt
        loop Mỗi 2 giây
            Browser->>Route: GET /api/vision/video/status
            Route->>VideoSvc: public_job(job_id)
            VideoSvc-->>Browser: {status: 'PROCESSING', progress: 45%}
        end
    end

    Browser->>Route: GET /api/vision/video/states (khi hoàn thành)
    Route->>VideoSvc: timeline(job_id)
    VideoSvc->>Disk: Đọc timeline.json
    VideoSvc-->>Browser: Trả về JSON timeline 24 ô qua các mốc thời gian
    Browser-->>User: Hiển thị bảng timeline và đồng bộ với thanh trượt video
```

---

### 2.4. Luồng Trợ lý Quản trị (AI Chatbot Flow with Zero-SQL Sandbox)
- **Mục đích:** Phân loại câu hỏi tự nhiên tiếng Việt, lấy số liệu bãi xe và trả lời Quản lý mà không cho phép LLM tiếp cận hay thực thi SQL.

```mermaid
sequenceDiagram
    autonumber
    actor Manager as Quản lý bãi xe
    participant Route as routes.py: api_chatbot()
    participant ChatSvc as chatbot.py
    participant Gemini as Google Gemini API
    participant DB as SQL Server (Hàm truy vấn định sẵn)

    Manager->>Route: POST /api/chatbot {message: "Hôm nay doanh thu bao nhiêu?"}
    Route->>ChatSvc: respond(message)

    alt Thử gọi Gemini NLU trích xuất ý định
        ChatSvc->>Gemini: Prompt NLU Intent Classification (Whitelist 10 intents)
        Gemini-->>ChatSvc: JSON {intent: "REVENUE", parameters: {period: "today"}, confidence: 0.95}
    else Gemini lỗi mạng / Hết quota / Confidence < 0.55
        ChatSvc->>ChatSvc: Chạy Fallback Regex nội bộ (Rule-based Intent Classifier)
    end

    note over ChatSvc,DB: Zero SQL Execution: Backend tự gọi hàm hardcoded tương ứng
    ChatSvc->>DB: Thực thi hàm get_revenue(date=today)
    DB-->>ChatSvc: Trả về verified_data: {turn_revenue: 1200000, month_revenue: 2000000, total: 3200000}

    alt Gọi Gemini Answer Formatter
        ChatSvc->>Gemini: Prompt NLG diễn đạt câu trả lời dựa DUY NHẤT trên verified_data
        Gemini-->>ChatSvc: "Hôm nay bãi xe đã thu tổng cộng 3.200.000đ..."
    else Gemini lỗi
        ChatSvc->>ChatSvc: Sử dụng template câu trả lời xác định (deterministic fallback)
    end

    ChatSvc-->>Route: Trả về cấu trúc JSON {ok: true, answer: "...", intent: "REVENUE", data: verified_data}
    Route-->>Manager: Hiển thị câu trả lời và số liệu minh chứng trên giao diện
```
