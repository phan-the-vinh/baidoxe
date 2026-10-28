# DANH MỤC API VÀ ĐÁNH GIÁ AN TOÀN THÔNG TIN (API AND SECURITY)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Nguồn đối chiếu:** `app/routes.py`, `app/services/common.py`, `app/models.py`  
> **Trạng thái:** Đã xác minh qua mã nguồn và test suite  

---

## 1. Danh Mục Endpoints Chi Tiết

Hệ thống phân tách thành 2 Blueprint chính:
- `web`: Render giao diện HTML thông qua Jinja2.
- `api` (prefix `/api`): Cung cấp các RESTful API trao đổi dữ liệu JSON và nhận upload file.

### 1.1. Bảng Tổng Hợp Web Routes

| Đường dẫn (URL) | Phương thức | Phân quyền (RBAC) | Mô tả chức năng |
|---|:---:|:---:|---|
| `/login` | `GET`, `POST` | Public (Unauthenticated) | Đăng nhập hệ thống, giới hạn thử sai (Rate limit 8 lần/5 phút). |
| `/logout` | `POST` | `@login_required` | Đăng xuất, xóa toàn bộ session hiện tại. |
| `/` | `GET` | `@login_required` | Trang Dashboard tổng quan: thống kê nhanh, lượt xe gần đây. |
| `/slots` | `GET` | `@login_required` | Sơ đồ bãi xe, hiển thị trạng thái ô và các cảnh báo chưa xử lý. |
| `/entry` | `GET` | `@login_required` | Màn hình tiếp nhận xe vào bãi (Check-in). |
| `/exit` | `GET` | `@login_required` | Màn hình cho xe ra bãi (Check-out) và tính phí. |
| `/visits` | `GET` | `@login_required` | Tra cứu lịch sử các lượt gửi xe, lọc theo biển số/mã vé/ngày. |
| `/visits/<int:id>` | `GET` | `@login_required` | Xem chi tiết 1 lượt gửi, in biên lai/phiếu gửi, đổi ô, báo mất vé. |
| `/monthly` | `GET` | `@login_required` | Tra cứu danh sách hồ sơ vé tháng. |
| `/monthly/new` | `GET` | `@manager_only` | Màn hình đăng ký vé tháng mới (Chỉ Quản lý). |
| `/monthly/<id>/renew` | `GET` | `@manager_only` | Màn hình gia hạn kỳ vé tháng (Chỉ Quản lý). |
| `/rates` | `GET` | `@manager_only` | Xem và cập nhật bảng giá dịch vụ (Chỉ Quản lý). |
| `/areas` | `GET` | `@manager_only` | Quản lý danh mục khu vực đỗ xe (Chỉ Quản lý). |
| `/users` | `GET` | `@manager_only` | Quản lý tài khoản nhân viên (Thêm/Sửa thông tin STAFF). |
| `/statistics` | `GET` | `@manager_only` | Báo cáo doanh thu, biểu đồ lưu lượng Chart.js (Chỉ Quản lý). |
| `/chatbot` | `GET` | `@manager_only` | Giao diện trợ lý đàm thoại AI (Chỉ Quản lý). |
| `/vision` | `GET` | `@login_required` | Giao diện giám sát video bãi xe và trạng thái model. |
| `/vision/video` | `GET` | `@login_required` | Tải và stream video phân tích cục bộ (`send_file`). |
| `/ai-demo` | `GET` | `@login_required` | Màn hình phát lại video demo AI D24 có timeline. |
| `/ai-demo/video` | `GET` | `@login_required` | Stream video demo pre-processed (`yolo_polygon_demo.mp4`). |
| `/backups` | `GET` | `@manager_only` | Danh sách lịch sử sao lưu CSDL (Chỉ Quản lý). |

---

### 1.2. Bảng Tổng Hợp REST API Endpoints (`/api/*`)

| Endpoint | Phương thức | Phân quyền | Tham số đầu vào (Payload) | Đầu ra (Response) |
|---|:---:|:---:|---|---|
| `/api/slots` | `GET` | Login | Không | Snapshot sức chứa, danh sách chi tiết 24 ô và xe đang đỗ. |
| `/api/plate-check` | `GET` | Login | `?plate=30A12345` | Kiểm tra trạng thái biển số (có vé tháng không, có lượt gửi dang dở không). |
| `/api/visits` | `POST` | Login | `plate`, `slot_code`, `plate_confirmed`, `slot_confirmed` | Tạo lượt gửi xe vào (`check_in`). Trả về `visit_id`. |
| `/api/visits/<id>/quote` | `GET` | Login | Không | Báo giá chi tiết cước phí tại thời điểm gọi. |
| `/api/visits/<id>/checkout` | `POST` | Login | `quoted_amount`, `vehicle_confirmed`, `paid_confirmed` | Hoàn tất lượt xe ra, chốt cước và thu tiền. |
| `/api/visits/<id>/move` | `POST` | Login | `target_slot`, `confirmed` | Chuyển xe sang ô đỗ mới trong bãi. |
| `/api/slots/<code>/confirm` | `POST` | Login | `state`, `confirmed`, `note` | Xác nhận thủ công trạng thái ô đỗ và đóng cảnh báo. |
| `/api/monthly` | `POST` | Manager | `plate`, `owner_name`, `amount`, `start_date`, `end_date` | Tạo mới hồ sơ và chu kỳ vé tháng đầu tiên. |
| `/api/monthly/<id>/renew` | `POST` | Manager | `amount`, `start_date`, `end_date` | Gia hạn chu kỳ tiếp theo cho vé tháng. |
| `/api/rates` | `POST` | Manager | `turn_fee`, `night_fee`, `monthly_fee` | Tạo phiên bản bảng giá mới (Append-only). |
| `/api/areas/<code>` | `POST` | Manager | `name`, `description` | Cập nhật thông tin mô tả khu vực. |
| `/api/users` | `POST` | Manager | `username`, `full_name`, `password` | Tạo mới tài khoản Nhân viên (`STAFF`). |
| `/api/users/<id>` | `POST` | Manager | `username`, `full_name`, `password` | Cập nhật tài khoản Nhân viên (`STAFF`). |
| `/api/backups` | `POST` | Manager | `confirmed=True` | Chạy sao lưu CSDL SQL Server native `.bak` kèm `VERIFYONLY`. |
| `/api/chatbot` | `POST` | Manager | `message: string` | Trả lời đàm thoại qua NLU / Verified Data. |
| `/api/vision/parking` | `POST` | Login | Multipart: `image` file | Phân tích ảnh bãi đỗ qua CNN (Legacy). |
| `/api/vision/plate` | `POST` | Login | Multipart: `image` hoặc `frames` | Gợi ý biển số qua Car/Plate Detector + OCR. |
| `/api/vision/status` | `GET` | Login | Không | Kiểm tra trạng thái sẵn sàng của các mô hình AI. |
| `/api/vision/video` | `POST` | Login | Multipart: `video` file (.mp4/.mov, max 250MB) | Tạo job phân tích video ngầm (Trả về HTTP 202). |
| `/api/vision/video/status` | `GET` | Login | Không (lấy job_id từ session) | Lấy tiến độ xử lý video ngầm (tiến độ %). |
| `/api/vision/video/states` | `GET` | Login | Không (lấy job_id từ session) | Lấy timeline trạng thái 24 ô sau khi phân tích xong. |
| `/api/ai-demo/states` | `GET` | Login | Không | Trả về timeline trạng thái của video demo pre-processed. |

---

## 2. Kiểm Soát Quyền Hạn (Authentication & RBAC)

### 2.1. Quản lý Phiên (Session Management)
- Sử dụng `Flask-Login` với `Session.permanent = True`.
- Cookie session được cấu hình bí mật qua `SECRET_KEY` ngẫu nhiên sinh ra từ `tools/setup_env.py`.

### 2.2. Phân Quyền Vai Trò (Role-Based Access Control)
Hệ thống chỉ có đúng 2 vai trò cố định, được kiểm soát bằng constraint `CHECK (role IN ('MANAGER','STAFF'))`:
- **`STAFF` (Nhân viên vận hành):**
  - Chỉ được thực hiện các thao tác vận hành hàng ngày: Check-in, Check-out, Đổi ô đỗ, Báo mất vé, Xem sơ đồ bãi xe, Tra cứu lịch sử và Gợi ý OCR.
  - **Bị chặn tuyệt đối ở cả giao diện lẫn backend** khi truy cập các chức năng quản trị: Đổi bảng giá, Tạo tài khoản, Xem doanh thu/thống kê tài chính, Chatbot, Cấu hình khu vực và Sao lưu CSDL.
- **`MANAGER` (Quản lý bãi xe):**
  - Toàn quyền trên mọi tính năng của hệ thống (bao gồm quyền vận hành và quyền quản trị).
  - Decorator `@manager_only` (`app/services/common.py:L19-26`) kiểm tra `current_user.role == 'MANAGER'`, nếu vi phạm lập tức trả về HTTP 403 Forbidden.

### 2.3. Cơ Chế Chống Brute-Force Đăng Nhập (`app/routes.py:L47-62`)
- Quản lý số lần đăng nhập thất bại trong bộ nhớ bằng `OrderedDict` đồng bộ hóa bằng `Lock`.
- Định danh bằng cặp khóa: `(Client IP, Username)`.
- Nếu đăng nhập sai 8 lần trong vòng 5 phút (300 giây): Hệ thống lập tức khóa và trả về HTTP 429 Too Many Requests ("Bạn đã thử nhiều lần. Hãy chờ 5 phút.").

---

## 3. Các Biện Pháp An Toàn Thông Tin Đã Xác Minh

1. **Bảo vệ CSRF (Cross-Site Request Forgery):**
   - Tích hợp `Flask-WTF`. Mọi request mutation (POST/PUT/DELETE) bắt buộc phải đính kèm header `X-CSRFToken` hoặc trường `csrf_token` trong form.
2. **Lưu trữ Mật khẩu An toàn:**
   - Sử dụng `werkzeug.security.generate_password_hash` và `check_password_hash` với thuật toán băm tiêu chuẩn (scrypt/pbkdf2:sha256). Mật khẩu không bao giờ được lưu dạng plaintext.
   - Kiểm tra độ dài mật khẩu tối thiểu 10 ký tự khi tạo tài khoản.
3. **Chống SQL Injection:**
   - 100% các câu truy vấn nghiệp vụ sử dụng SQLAlchemy 2.0 ORM và tham số hóa (Parameterized Queries). Không có hiện tượng ghép chuỗi SQL từ đầu vào người dùng.
4. **Cô lập Mô hình Ngôn ngữ Lớn (LLM Sandbox):**
   - Google Gemini API hoàn toàn không được cấp quyền thực thi SQL. Toàn bộ dữ liệu gửi tới Gemini là dữ liệu đã qua xác thực (Verified Data) do backend chủ động truy vấn bằng các hàm cố định.

---

## 4. Các Rủi Ro An Toàn Thông Tin Cần Lưu Ý

- **[Medium] Truyền thông HTTP Plaintext:** Mặc định `run.py` chạy qua Waitress lắng nghe cổng HTTP không mã hóa (`http://127.0.0.1:5000`). Nếu mở cổng phục vụ qua mạng LAN nội bộ, cần đặt phía sau Reverse Proxy (như Nginx) có cấu hình SSL/TLS (HTTPS).
- **[Low-Medium] Quản lý Tệp Cấu hình `.env`:** Tệp `.env` chứa mật khẩu quản trị và tài khoản CSDL SQL Server. Cần bảo vệ phân quyền đọc file ở mức hệ điều hành Windows (NTFS Permissions).
