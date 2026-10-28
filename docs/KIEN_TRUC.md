# Kiến trúc và dữ liệu

Flask application factory `create_app` tại `app/__init__.py`; các route web/API tách bằng
Blueprint, logic nghiệp vụ ở services. SQLAlchemy + pyodbc là kết nối SQL Server.
Jinja tạo HTML; JavaScript gọi API cùng origin, gửi CSRF token, hiển thị lỗi cạnh
form. Bootstrap 5.3.3 và Chart.js 4.4.7 được lưu local.

## Thành phần dữ liệu

| Bảng | Dữ liệu / liên kết |
|---|---|
| users | Tên đăng nhập unique, mật khẩu hash, họ tên, role MANAGER/STAFF |
| areas | Khóa A/B, tên và mô tả |
| slots | Khóa A01…B10, FK khu, EMPTY/OCCUPIED; kết quả AI gần nhất và cờ cảnh báo |
| rates | Các phiên bản giá lượt/đêm/tháng, người tạo, thời điểm |
| monthly_passes | Mã vé, biển số unique, chủ xe |
| pass_periods | FK vé tháng, ngày đầu/cuối và phí đã thu |
| visits | Vé lượt/biển/ô, vào/ra, FK rate và kỳ vé tháng, chi tiết phí, xác minh mất vé |
| payments | Tiền mặt đã xác nhận, FK visit hoặc pass_period, người thu |
| observations | Dự đoán từng ROI, thời điểm, confidence, được chấp nhận hay bỏ qua |
| alerts | Cảnh báo ô đỗ, nguyên nhân, người và lý do xử lý |
| audit_logs | Nhật ký hành động, người thao tác, dữ liệu mô tả; không mật khẩu/API key |
| backup_runs | Lịch sử file .bak, trạng thái thực thi/xác minh |
| site_lock | Khóa ghi chung cho giao dịch bãi 20 ô |

Không lưu dataset huấn luyện trong SQL Server. File ảnh upload được đọc trong bộ
nhớ và không ghi vào static. Không xuất raw exception hay chuỗi kết nối về UI.

## Giao dịch

SQL Server dùng `site_lock WITH (UPDLOCK, HOLDLOCK)` trước các kiểm tra nghiệp vụ
ghi. Các insert/update và nhật ký cùng transaction. Filtered unique index đảm
bảo không tồn tại hai lượt có `exited_at IS NULL` cho cùng xe/ô. Vé tháng chỉ nối
thêm kỳ sau kỳ gần nhất. Bảng giá nối thêm phiên bản, không sửa giá đã áp dụng.

Sao lưu chạy riêng AUTOCOMMIT vì SQL Server không cho BACKUP trong transaction
nghiệp vụ. Đọc hết kết quả ODBC, tiếp tục VERIFYONLY, rồi mới ghi SUCCESS.

## CNN

Input mặc định 96×96×3 RGB, augmentation nhẹ, Rescaling, ba khối Conv2D + MaxPool
(16/32/64 filters), Flatten, Dense 64, Dropout 0,4, Dense sigmoid. Trọng số khởi tạo
ngẫu nhiên. Loss binary cross-entropy, Adam, class weights theo train. Early
stopping/checkpoint/LR schedule chỉ theo validation loss.

Các script cùng dùng `rgb_input` và cách crop polygon. Inference `training=False`
nên tắt augmentation/dropout. Model load lazy một lần, cache theo mtime model và
metadata; không train trong Flask. Chỉ nhận một batch inference tại một thời điểm
để tránh camera gửi dồn nhiều request. Kết quả cũ hơn thay đổi dữ liệu bị bỏ qua.

## AI Chatbot dùng Gemini

SQL Server chỉ được backend truy vấn qua các hàm đọc cố định; Gemini không nhận
kết nối hay SQL tool. Intent router xác định truy vấn hợp lệ, backend lấy dữ liệu
có cấu trúc rồi Gemini mới diễn giải. Khi Gemini lỗi, câu trả lời fallback vẫn
dựa trên dữ liệu thực. Chỉ Quản lý được truy cập `/chatbot`.

## Giới hạn vận hành

- Dành cho localhost, một tiến trình Waitress; không public deployment.
- Video xử lý lấy mẫu từng khung từ trình duyệt; không phải hệ thống camera giám sát chạy nền 24/7.
- ROI phải đúng camera/góc nhìn/kích thước, không tự định vị lại khi camera dịch chuyển.
- Lịch sử observations có thể tăng theo thời gian; demo chưa có chính sách xóa/tổng hợp dữ liệu dài hạn.
- Không có web restore, quên mật khẩu, tự đăng ký, khóa/xóa người dùng hoặc migration tự động vì ngoài đặc tả.
- SQLite chỉ trong unit tests, không dùng làm bản vận hành thay SQL Server.
