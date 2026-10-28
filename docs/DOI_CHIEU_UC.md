# Đối chiếu UC001–UC020

| UC | Giao diện | Xử lý chính / tệp |
|---|---|---|
| UC001 Đăng nhập | `/login`, đăng xuất | Flask-Login, hash mật khẩu, CSRF, giới hạn thử sai trong tiến trình |
| UC002 Tài khoản người dùng | `/users` | `POST /api/users`, sửa `/api/users/<id>`; không khóa/xóa |
| UC003 Khu vực | `/areas` | Sửa tên/mô tả A/B; ràng buộc khu cố định |
| UC004 Vị trí | `/slots` | Sơ đồ 20 ô, trạng thái nghiệp vụ/AI, xác nhận thủ công, chuyển ô từ chi tiết lượt |
| UC005 Xe vào | `/entry` | `parking.check_in`, xác nhận biển, check trùng, nhận vé tháng, ghi transaction |
| UC006 Gợi ý/xác nhận ô | Trong `/entry` | Danh sách ô đủ điều kiện, gợi ý đầu, bắt buộc xác nhận; kiểm tra lại khi lưu |
| UC007 Xe ra | `/exit` | Quote, đối chiếu xe, xác nhận tiền, `parking.check_out`, giải phóng ô |
| UC008 Bảng giá | `/rates` | Phiên bản giá bất biến; visit giữ rate_id lúc vào |
| UC009 Vé tháng | Đã loại bỏ | Đã loại bỏ hoàn toàn khỏi hệ thống |
| UC010 Mất vé | Đã loại bỏ | Đã loại bỏ luồng mất vé; giữ nguyên quy trình xe ra thông thường |
| UC011 AI ô đỗ | `/vision` | Ảnh/video/webcam → OpenCV/ROI → CNN tự train → 20 dự đoán |
| UC012 AI bất thường | `/slots` | Cảnh báo thấp confidence/mâu thuẫn; người dùng kiểm tra và xác nhận |
| UC013 Báo cáo AI | `/chatbot` | Intent lưu lượng/doanh thu → hàm truy vấn chỉ đọc → Gemini diễn giải hoặc fallback |
| UC014 Hỏi đáp | `/chatbot` | Intent router giới hạn phạm vi; Gemini không được sinh hoặc thực thi SQL |
| UC015 Gợi ý nhân sự | Không còn màn hình riêng | Không tự phân ca; Chatbot chỉ mô tả chức năng và dữ liệu được hỗ trợ |
| UC016 Tra lượt gửi | `/visits`, `/visits/<id>` | Biển/mã vé, ngày vào, trạng thái, phân trang, chi tiết, in phiếu |
| UC017 Tra vé tháng | Đã loại bỏ | Đã loại bỏ cùng chức năng Vé tháng |
| UC018 Thống kê | `/statistics` | Lượt, thực thu, mức lấp đầy, bảng giờ và Chart.js |
| UC019 Sao lưu | `/backups` | BACKUP COPY_ONLY CHECKSUM và RESTORE VERIFYONLY; lưu lịch sử |
| UC020 OCR biển số | Khối OCR trong `/entry`, `/exit` | OpenCV/Tesseract, ảnh/webcam, sửa/nhập tay và xác nhận trước dùng |

## Phân quyền

| Thao tác | Người dùng | Người quản lý |
|---|:---:|:---:|
| Đăng nhập, đăng xuất | Có | Có |
| Vận hành xe vào/ra | Có | Có, hỗ trợ |
| Vị trí, AI ô đỗ, xử lý bất thường, OCR | Có | Có |
| Tra lượt gửi | Có | Có |
| Bảng giá, người dùng, sửa khu | Không | Có |
| Thống kê doanh thu và AI Chatbot | Không | Có |
| Sao lưu | Không | Có |

Quyền kiểm tra ở backend; ẩn menu chỉ là cách trình bày. Trang tổng quan Người dùng
chỉ có chỉ số vận hành hiện tại, không có thống kê doanh thu toàn bãi.

## Bổ sung phục vụ triển khai, không mở rộng nghiệp vụ

- In phiếu HTML từ dữ liệu lượt gửi, phục vụ demo mã vé/thu tiền.
- Nhật ký thao tác nội bộ, khóa giao dịch, CSRF, kiểm tra dữ liệu và chống gửi lặp.
- Metadata/hash model để không hiển thị metrics thuộc model khác.
- Công cụ cấu hình môi trường, ROI, cắt/chia dataset và kiểm thử.

Không bổ sung chức năng mở rộng như QR, nhiều bãi, phân ca tự động hoặc quản lý xe máy.
