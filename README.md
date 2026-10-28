# ParkView — Hệ thống quản lý bãi đỗ ô tô thông minh tích hợp AI

ParkView (`parking_ai`) là giải pháp quản lý bãi đỗ ô tô chạy trên nền tảng **Flask / Python**, sử dụng cơ sở dữ liệu **Microsoft SQL Server**, tích hợp các mô-đun thị giác máy tính (Computer Vision) giám sát trạng thái vị trí đỗ, nhận diện ký tự biển số (OCR) và trợ lý ảo (AI Chatbot) hỗ trợ vận hành và phục vụ khách hàng.

---

## 1. Mục tiêu và tính năng chính

Hệ thống được thiết kế đáp ứng các mô hình bãi đỗ xe ngoài trời và trong nhà (trường học, trung tâm thương mại, tòa nhà, bãi đỗ đơn vị kinh doanh), bao gồm các phân hệ chính:

### A. Phân quyền ba vai trò người dùng chuẩn hóa
- **Quản trị viên (`ADMIN`)**:
  - Quản lý danh sách người dùng, cấp và quản lý tài khoản cho nhân viên vận hành (`STAFF`).
  - Cấu hình bảng giá dịch vụ gửi xe theo khung giờ và qua đêm.
  - Theo dõi báo cáo thống kê trực quan (doanh thu, lượt xe vào/ra, tỷ lệ lấp đầy bãi xe) qua biểu đồ Chart.js.
  - Quản trị sao lưu cơ sở dữ liệu SQL Server dạng file `.bak` với cơ chế kiểm tra toàn vẹn `RESTORE VERIFYONLY`.
  - Toàn quyền tra cứu dữ liệu và khai thác trợ lý AI Chatbot quản trị.
- **Nhân viên vận hành (`STAFF`)**:
  - Thực hiện tiếp nhận xe vào bãi: quét/chọn ảnh biển số, gợi ý ký tự qua OCR, chọn vị trí đỗ và phát hành phiếu gửi xe.
  - Thực hiện quy trình xe ra: tra cứu lượt gửi theo biển số hoặc mã vé, tính toán biểu phí gửi xe chính xác, xác nhận thu tiền mặt và giải phóng vị trí đỗ.
  - Giám sát sơ đồ 24 vị trí đỗ khu D (D01–D24), kiểm tra trạng thái thực tế và xử lý cảnh báo sai lệch vị trí đỗ.
  - Khai thác AI Chatbot hỗ trợ tra cứu nghiệp vụ vận hành nhanh chóng.
- **Khách hàng (`CUSTOMER`)**:
  - Tự đăng ký tài khoản trực tuyến qua cổng đăng ký (`/register`).
  - Sử dụng giao diện Cổng thông tin khách hàng (`/customer`): tra cứu lịch sử gửi xe theo biển số cá nhân đã được xác minh.
  - Xem thông tin số lượng vị trí đỗ đang còn trống trong thời gian thực.
  - Tương tác với AI Chatbot để tra cứu thông tin bảng giá, quy định bãi đỗ và tình trạng lượt xe cá nhân.

### B. Thị giác máy tính & AI
- **Giám sát ô đỗ (Slot Detection)**: Tích hợp mô hình thị giác máy tính kết hợp cấu hình đa giác ROI (`config/demo_parking/0922_1_polygons.json`) cho 24 vị trí đỗ, hỗ trợ phân tích hình ảnh và video giám sát theo luồng thời gian thực.
- **Nhận diện biển số (License Plate OCR)**: Sử dụng OpenCV trích xuất vùng biển số kết hợp Tesseract OCR nhận diện ký tự tự động, hỗ trợ nhân viên đối chiếu và xác nhận nhanh chóng.
- **AI Chatbot đa nền tảng**:
  - Hỗ trợ mô hình nội bộ qua **Ollama Local** (mặc định `qwen3:4b` hoặc mô hình tương thích), vận hành hoàn toàn offline, bảo mật dữ liệu tuyệt đối.
  - Hỗ trợ tích hợp **Google Gemini API** qua cơ chế Intent NLU an toàn.
  - *Nguyên tắc an toàn dữ liệu*: Chatbot phân loại intent câu hỏi, chỉ truy vấn dữ liệu từ cơ sở dữ liệu qua các hàm service nội bộ được kiểm soát phân quyền chặt chẽ; AI tuyệt đối không có quyền kết nối trực tiếp hoặc tự sinh mã thực thi SQL.

*(Lưu ý: Luồng nghiệp vụ Vé tháng và Xe ra - Mất vé đã được loại bỏ hoàn toàn khỏi hệ thống để chuẩn hóa quy trình vé lượt đồng nhất).*

---

## 2. Công nghệ sử dụng

- **Backend**: Python 3.11, Flask, SQLAlchemy, Waitress WSGI Server.
- **Cơ sở dữ liệu**: Microsoft SQL Server (SQLEXPRESS / Developer), kết nối qua `pyodbc` và Microsoft ODBC Driver 18 for SQL Server.
- **AI & Computer Vision**: OpenCV, Ultralytics YOLOv8 / TensorFlow Keras, Tesseract OCR, Shapely.
- **AI Chatbot & LLM**: Ollama Local API, Google GenAI SDK.
- **Frontend**: HTML5, CSS3, JavaScript (Vanilla), Bootstrap 5, Chart.js (toàn bộ thư viện được lưu trữ local trong `app/static/vendor`, không phụ thuộc CDN ngoài).

---

## 3. Điều kiện môi trường và cài đặt

### Yêu cầu hệ thống
1. Hệ điều hành: Windows 10/11 64-bit.
2. Python: Phiên bản **3.11** (khuyến nghị sử dụng Conda hoặc môi trường ảo venv riêng biệt).
3. Microsoft SQL Server: Bản Express hoặc Developer với Database Engine đang chạy.
4. Microsoft ODBC Driver 18 for SQL Server (bản x64).
5. *(Tùy chọn)* Tesseract OCR (Windows x64) nếu sử dụng chức năng đọc ký tự biển số tự động.
6. *(Tùy chọn)* Ollama nếu sử dụng tính năng Chatbot AI Local offline.

### Các bước cài đặt dependency

1. Khởi tạo và kích hoạt môi trường Python:
```powershell
conda create -n parking_ai python=3.11 -y
conda activate parking_ai
```

2. Cài đặt các gói thư viện chuẩn của dự án:
```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Cài đặt các gói mở rộng AI hoặc kiểm thử (nếu cần):
```powershell
# Gói mở rộng cho huấn luyện và thị giác máy tính
python -m pip install -r requirements-ai.txt

# Gói phục vụ chạy kiểm thử tự động
python -m pip install -r requirements-dev.txt
```

---

## 4. Cấu hình biến môi trường (`.env`)

Hệ thống quản lý toàn bộ tham số qua biến môi trường. Không bao giờ commit file `.env` chứa thông tin nhạy cảm lên Git.

1. Tạo file `.env` từ file mẫu:
```powershell
copy .env.example .env
```

2. Mở file `.env` và thiết lập các thông số phù hợp:

```dotenv
# Khóa bảo mật phiên làm việc của Flask
SECRET_KEY=dien_chuoi_ngau_nhien_tai_day

# Cấu hình kết nối SQL Server
DB_SERVER=localhost\SQLEXPRESS
DB_NAME=ParkingAI
DB_DRIVER=ODBC Driver 18 for SQL Server

# Xác thực Windows: đặt true (không cần điền DB_USER, DB_PASSWORD)
# Xác thực SQL Login: đặt false và điền DB_USER, DB_PASSWORD
DB_TRUSTED_CONNECTION=true
DB_USER=
DB_PASSWORD=
DB_ENCRYPT=yes
DB_TRUST_SERVER_CERTIFICATE=yes

# Tài khoản Quản trị viên khởi tạo khi chạy init-db
ADMIN_USERNAME=admin
ADMIN_PASSWORD=dien_mat_khau_khoi_tao_tai_day
ADMIN_FULL_NAME=Quan tri vien

# Cấu hình Chatbot AI Local qua Ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_LLM_MODEL=qwen3:4b

# Cấu hình Chatbot Google Gemini (Tùy chọn - để trống nếu không dùng)
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash

# Đường dẫn Tesseract OCR (nếu chưa có trong biến môi trường PATH)
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe

# Thư mục lưu bản sao lưu cơ sở dữ liệu trên máy chủ SQL Server
BACKUP_DIR=C:\ParkingAI_Backups
PORT=5000
```

---

## 5. Khởi tạo cơ sở dữ liệu và khởi chạy ứng dụng

### Khởi tạo cơ sở dữ liệu
1. Mở SQL Server Management Studio (SSMS), kết nối tới SQL Server của bạn.
2. Mở tệp [create_database.sql](database/create_database.sql) và nhấn **Execute** để tạo cơ sở dữ liệu `ParkingAI`.
3. Mở terminal tại thư mục gốc của dự án và chạy lệnh khởi tạo các bảng và dữ liệu danh mục mẫu:
```powershell
python -m flask --app app:create_app init-db
```
Lệnh này sẽ tạo cấu trúc 13 bảng nghiệp vụ, thiết lập bảng giá mặc định, khởi tạo 24 vị trí đỗ khu D (D01–D24) và tạo tài khoản Quản trị viên ban đầu theo thông tin trong file `.env`.

### Khởi chạy ứng dụng
Chạy server phục vụ web:
```powershell
python run.py
```
Sau khi khởi chạy thành công, truy cập giao diện tại: **[http://127.0.0.1:5000](http://127.0.0.1:5000)**.

---

## 6. Kiểm tra cơ bản và xác minh hệ thống

1. **Kiểm tra kết nối môi trường và cơ sở dữ liệu:**
```powershell
python tools/check_environment.py --database
```
Kết quả mong đợi: Báo cáo kết nối SQL Server thành công, nhận diện đúng phiên bản ODBC Driver và cấu trúc dữ liệu.

2. **Chạy kiểm thử tự động:**
```powershell
python -m pytest tests/
```
Bộ kiểm thử tự động xác thực các quy tắc nghiệp vụ: phân quyền vai trò (RBAC), tính toán biểu phí gửi xe, luồng xe vào/ra, NLU Chatbot và xử lý tọa độ đa giác ROI.

---

## 7. Giới hạn đã biết và lưu ý vận hành

- **Phạm vi triển khai**: Phiên bản hiện tại được tối ưu hóa cho mô hình chạy tại chỗ (local/on-premise) phục vụ công tác quản lý và minh họa giải pháp cho một điểm bãi đỗ xe.
- **Trọng số mô hình và tập dữ liệu lớn**: Tập dữ liệu huấn luyện nặng (>3GB) và các file checkpoint AI nhị phân (`.pt`, `.keras`) được quản lý ngoài kho lưu trữ Git để đảm bảo hiệu suất và kích thước tinh gọn của repository.
- **Cơ chế gợi ý biển số**: Module OCR đóng vai trò trợ lý nhập liệu, kết quả quét phụ thuộc vào chất lượng ánh sáng và góc chụp của camera; nhân viên vận hành luôn có quyền đối chiếu thực tế và chỉnh sửa trước khi xác nhận.
- **Bảo mật cơ sở dữ liệu**: Lệnh sao lưu cơ sở dữ liệu sử dụng tùy chọn an toàn `CHECKSUM` và tự động kiểm tra lại với `RESTORE VERIFYONLY`. Tài khoản dịch vụ của SQL Server cần được cấp quyền ghi vào thư mục `BACKUP_DIR`.
