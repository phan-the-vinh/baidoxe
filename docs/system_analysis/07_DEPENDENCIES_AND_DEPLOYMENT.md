# PHỤ THUỘC VÀ QUY TRÌNH TRIỂN KHAI (DEPENDENCIES AND DEPLOYMENT)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Nền tảng mục tiêu:** Windows 10/11 64-bit, Python 3.11, Microsoft SQL Server  
> **Trạng thái:** Đã xác minh qua cấu hình `requirements.txt`, `run.py`, `app/config.py`  

---

## 1. Yêu Cầu Môi Trường và Runtime

1. **Hệ điều hành:** Windows 10/11 64-bit native (hoặc WSL2 nếu cần môi trường huấn luyện GPU cho TensorFlow/YOLO).
2. **Ngôn ngữ:** Python **3.11** (khuyến nghị chạy trong môi trường Conda hoặc Python venv riêng biệt).
3. **Hệ quản trị CSDL:**
   - Microsoft SQL Server Database Engine (bản Developer, Standard hoặc Express).
   - SQL Server Management Studio (SSMS) để quản trị.
4. **Trình điều khiển CSDL (Driver):**
   - Microsoft **ODBC Driver 18 for SQL Server** (bản x64). Bắt buộc để PyODBC kết nối được với SQL Server.
5. **Công cụ OCR bên ngoài:**
   - **Tesseract OCR Engine** cài đặt trên Windows (`tesseract.exe`).
6. **Môi trường YOLO riêng (Nếu dùng tính năng nâng cao):**
   - Python environment riêng (ví dụ `yolo_train`) chứa `ultralytics` và PyTorch để chạy các worker trong `tools/`.

---

## 2. Danh Mục Thư Viện Python (Package Dependencies)

### 2.1. Thư viện Vận hành Ứng dụng Web (`requirements.txt`)

| Gói thư viện | Phiên bản ghim | Mục đích sử dụng kỹ thuật |
|---|---|---|
| `Flask` | `3.1.2` | Core Web Framework, routing và application context |
| `Flask-SQLAlchemy` | `3.1.1` | Tích hợp SQLAlchemy ORM vào Flask |
| `SQLAlchemy` | `2.0.43` | ORM, Query Builder và quản lý Connection Pool |
| `Flask-Login` | `0.6.3` | Quản lý phiên xác thực người dùng và User Session |
| `Flask-WTF` | `1.2.2` | Bảo vệ biểu mẫu và xác thực CSRF Token |
| `python-dotenv` | `1.1.1` | Tự động đọc biến môi trường từ tệp `.env` |
| `pyodbc` | `5.2.0` | Driver tầng thấp kết nối Python với ODBC Driver 18 của SQL Server |
| `waitress` | `3.0.2` | Production-grade WSGI Server chạy local trên Windows |
| `tzdata` | `>=2025.2` | Dữ liệu múi giờ quốc tế phục vụ ZoneInfo('Asia/Ho_Chi_Minh') |
| `numpy` | `>=1.26,<2.3` | Xử lý ma trận ảnh và mảng số học |
| `opencv-python` | `4.11.0.86` | Tiền xử lý ảnh, cắt polygon ROI, chuyển đổi không gian màu |
| `pytesseract` | `0.3.13` | Python wrapper giao tiếp với binary Tesseract OCR |
| `Pillow` | `>=11.0,<13` | Kiểm tra định dạng và kích thước ảnh chống Decompression Bomb |
| `google-genai` | `>=1.30,<2` | Google GenAI SDK chính thức kết nối Google Gemini API |

### 2.2. Thư viện Huấn luyện AI Mở rộng (`requirements-ai.txt`)
- `tensorflow>=2.19,<2.21`: Huấn luyện và thực thi mô hình CNN Keras 96x96.
- `scikit-learn>=1.6,<2`: Tính toán metric đánh giá (Confusion Matrix, Precision/Recall).
- `matplotlib>=3.9,<4`: Vẽ biểu đồ loss/accuracy trong quá trình train.

### 2.3. Thư viện Phát triển và Kiểm thử (`requirements-dev.txt`)
- `pytest`: Framework thực thi bộ 59 test cases tự động.

---

## 3. Danh Mục Biến Môi Trường Cấu Hình (`.env`)

Tuyệt đối không lưu giá trị bí mật trong tài liệu. Dưới đây là danh mục tên biến và mục đích sử dụng:

| Tên biến môi trường | Giá trị mặc định / Ví dụ | Bắt buộc | Mục đích sử dụng kỹ thuật |
|---|---|:---:|---|
| `SECRET_KEY` | *(Sinh ngẫu nhiên 64 hex)* | Có | Mã hóa Session cookie và CSRF token |
| `DB_SERVER` | `localhost\SQLEXPRESS` | Có | Tên Server/Instance của SQL Server |
| `DB_NAME` | `ParkingAI` | Có | Tên Database trên SQL Server |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server`| Có | Tên ODBC Driver đã cài trên Windows |
| `DB_TRUSTED_CONNECTION` | `true` | Có | `true`: Windows Auth; `false`: dùng DB_USER/PWD |
| `DB_USER` | Rỗng | Tùy chọn | Tài khoản SQL Server Login (khi không dùng Windows Auth) |
| `DB_PASSWORD` | Rỗng | Tùy chọn | Mật khẩu SQL Server Login |
| `DB_ENCRYPT` | `yes` | Có | Bật mã hóa đường truyền ODBC |
| `DB_TRUST_SERVER_CERTIFICATE` | `yes` | Có | Cho phép chứng chỉ tự ký trên máy local |
| `ADMIN_USERNAME` | `admin` | Có | Tên đăng nhập tài khoản Quản lý đầu tiên |
| `ADMIN_PASSWORD` | *(Tối thiểu 10 ký tự)* | Có | Mật khẩu khởi tạo Quản lý khi chạy `init-db` |
| `ADMIN_FULL_NAME` | `Quan ly bai xe` | Không | Họ tên hiển thị của Quản lý |
| `GEMINI_API_KEY` | Rỗng | Tùy chọn | API Key gọi Google Gemini AI Chatbot |
| `GEMINI_MODEL` | `gemini-1.5-flash` / `gemini-2.0-flash` | Tùy chọn | Tên model Gemini muốn sử dụng |
| `AI_CONFIDENCE_THRESHOLD` | `0.80` | Không | Ngưỡng điểm tin cậy để kích hoạt cảnh báo AI |
| `PARKING_MODEL_PATH` | `ai_models/parking_classifier.keras` | Không | Đường dẫn tới file model Keras CNN |
| `PARKING_ROI_PATH` | `config/parking_slots.json` | Không | Đường dẫn tới file cấu hình polygon ROI |
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Tùy chọn | Đường dẫn tuyệt đối tới file binary tesseract |
| `BACKUP_DIR` | `C:\ParkingAI_Backups` | Có | Thư mục trên ổ đĩa để SQL Server ghi file `.bak` |
| `PORT` | `5000` | Không | Cổng HTTP mà Waitress server lắng nghe |
| `YOLO_TRAIN_PYTHON` | `D:\miniconda\envs\yolo_train\python.exe` | Tùy chọn | Python interpreter chứa Ultralytics YOLO |

---

## 4. Quy Trình Khởi Tạo và Vận Hành Chuẩn (Step-by-Step)

### Bước 1: Chuẩn bị Môi trường Python
```powershell
conda create -n parking_ai python=3.11 -y
conda activate parking_ai
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Bước 2: Cấu hình Môi trường `.env`
```powershell
python tools/setup_env.py
```
Nhập mật khẩu cho tài khoản `admin` (tối thiểu 10 ký tự). Script sẽ sinh khóa ngẫu nhiên và tạo file `.env`. Sau đó mở `.env` kiểm tra lại `DB_SERVER` và `BACKUP_DIR`.

### Bước 3: Tạo CSDL trên SQL Server & Seed Dữ liệu
- Mở SSMS, kết nối tới SQL Server và chạy nội dung file `database/create_database.sql` để tạo database `ParkingAI`.
- Quay lại PowerShell chạy lệnh khởi tạo:
```powershell
flask --app run.py init-db
```
Lệnh này sẽ tạo đầy đủ 13 bảng dữ liệu và chèn các bản ghi cấu hình ban đầu (24 ô đỗ Khu D, bảng giá mặc định, tài khoản Quản lý).

### Bước 4: Khởi chạy Ứng dụng
```powershell
python run.py
```
Giao diện sẽ sẵn sàng tại địa chỉ: `http://127.0.0.1:5000`.

---

## 5. Giới Hạn Kiến Trúc Triển Khai
- **Chạy Single-Process Cục bộ:** Ứng dụng được thiết kế chạy trên 1 máy tính duy nhất qua Waitress WSGI (`threads=6`), phục vụ nhân viên tại bốt bảo vệ.
- **Không hỗ trợ Cluster / Multi-Instance:** Do sử dụng khóa vật lý bi quan `site_lock` trên SQL Server và quản lý Job phân tích video qua bộ nhớ trong (`_jobs: dict`), việc triển khai nhiều instance phía sau Load Balancer sẽ làm phân mảnh trạng thái bộ nhớ.
