# Kết quả kiểm thử và phạm vi xác minh

Ngày thực hiện: 16/09/2026. Đây là minh chứng kiểm tra mã nguồn, không phải chứng
nhận đã triển khai trên máy người dùng hoặc benchmark AI trên bãi xe thật.

## Kết quả đã thực hiện

| Hạng mục | Kết quả | Phạm vi |
|---|---|---|
| Bộ pytest | 59 ca đạt | Nghiệp vụ, quyền truy cập, dữ liệu và AI lỗi; SQLite tạm thời |
| Tính phí | Đạt các mốc 2 giờ, 2 giờ + 1 giây, đủ/chưa đủ 5 giờ đêm, nhiều đêm, vé tháng | Quy tắc công khai trong NGHIEP_VU.md |
| Transaction nghiệp vụ | Các tình huống thất bại giữ dữ liệu, từ chối trùng xe/ô, thu lặp | Chưa chứng minh cạnh tranh trên SQL Server |
| Vé tháng | Từ chối trùng hồ sơ/chồng kỳ, giữ khoảng nghỉ, chốt quyền lúc vào | Dữ liệu kiểm thử |
| AI an toàn nghiệp vụ | Mâu thuẫn/điểm thấp tạo cảnh báo; khung cũ bỏ qua; không giải phóng ô có lượt gửi | Dự đoán được đưa vào test để kiểm tra luật, không coi là inference thật |
| CSRF | Thiếu token bị từ chối; token thật dùng đăng nhập và API thành công | Kiểm tra cả test client và trình duyệt |
| Browser end-to-end | Đăng nhập → kiểm tra biển → chọn A01 → xe vào → tính 20.000đ → thu tiền → phiếu thanh toán | Chromium headless + Waitress + SQLite chỉ dùng kiểm thử |
| Giao diện | Render login, dashboard, xe ra, thống kê, AI; không lỗi JavaScript trong luồng đã chạy | Desktop 1440×1080; mobile 390×844, không tràn chiều ngang toàn trang |
| MSSQL DDL | Compile thành công 13 bảng, index có filter, UPDLOCK/HOLDLOCK | Kiểm tra dialect, không thực thi SQL Server thật |
| CNN train/evaluate | Chạy thật 2 epoch, lưu `.keras`, metadata, history, plots, metrics | 24 ảnh train + 8 validation + 8 test **tổng hợp chỉ để kiểm tra luồng** |
| CNN load/predict | Nạp lại model vừa train, chạy 20 ROI, tạo overlay | Ảnh tổng hợp; không đánh giá chất lượng bãi xe thật |
| Thiếu model/dataset | Dừng rõ lỗi, không ghi dự đoán hoặc tự tạo metrics | Sản phẩm bàn giao mặc định |
| Cú pháp Python/JavaScript | Kiểm tra thành công | Các tệp source của dự án |

Ảnh tổng hợp, model và metrics từ smoke test **không nằm trong gói sản phẩm**.
Không đưa accuracy/precision/recall/F1 của thử nghiệm tổng hợp vào báo cáo chất
lượng mô hình. Thư mục `ai_models` và `ai/results` để trống cho lần train thật.

Ảnh `docs/screenshots` được chụp từ ứng dụng thật với dữ liệu kiểm thử và có banner
ghi rõ SQLite tạm thời. Chúng minh họa giao diện, không chứng minh có bãi xe thật.

## Môi trường kiểm tra

- Python 3.12; Flask 3.1.2; Flask-SQLAlchemy 3.1.1; SQLAlchemy 2.0.43.
- Flask-WTF 1.2.2; Flask-Login 0.6.3; Waitress 3.0.2.
- TensorFlow CPU 2.20.0; Keras 3.15.1; NumPy 2.2.6; OpenCV headless 4.12.0.
- Trình duyệt Chromium headless cho kiểm thử UI. Hướng dẫn người dùng dùng Python
  3.11 Windows; công cụ ROI dùng OpenCV có GUI, không phải bản headless của máy kiểm tra.

## Cần xác nhận trên máy Windows / dữ liệu thật

1. **SQL Server:** kết nối ODBC, tạo 13 bảng, đúng 20 ô, thao tác vào/ra/gia hạn.
2. **Cạnh tranh:** hai yêu cầu cùng lúc phải chỉ tạo một lượt cho một xe/ô.
3. **Backup:** quyền BACKUP/VERIFYONLY, quyền ghi của tài khoản dịch vụ SQL Server,
   tệp `.bak` thật, diễn tập restore sang database riêng.
4. **Camera/ROI:** mở cửa sổ editor, click/lưu, ảnh/video đúng kích thước và góc camera.
5. **CNN thật:** thu thập, gán nhãn, chia theo phiên, train, đánh giá test giữ riêng.
6. **OCR:** ảnh biển số thật ở nhiều góc/sáng; kiểm tra Tesseract trên Windows.
7. **AI Chatbot:** API key/model hợp lệ, intent Gemini và fallback backend; cần
   kiểm tra quota/timeout bằng khóa của nhóm.

## Chạy lại test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Unit test không sửa database SQL Server cấu hình trong `.env`.

## Kiểm thử cạnh tranh trên SQL Server riêng

Chỉ làm với CSDL dành riêng cho test, không trỏ vào dữ liệu vận hành:

1. Tạo database trống `ParkingAI_Test` trong SSMS.
2. Mở terminal mới tại thư mục dự án, kích hoạt môi trường rồi chạy:

```powershell
$env:DB_NAME = "ParkingAI_Test"
python -m flask --app app:create_app init-db
python tools/sqlserver_acceptance.py
Remove-Item Env:DB_NAME
```

Script chỉ chấp nhận tên database kết thúc `_Test`, không tự xóa/tạo database.
Nó chạy hai tình huống cạnh tranh, đóng lượt thành công bằng luồng xe ra và giữ
lịch sử kiểm thử trong CSDL Test. Lệnh `Remove-Item` chỉ bỏ biến môi trường tạm của
terminal này; lần chạy tiếp dùng DB_NAME trong `.env`. Không dùng kết quả CSDL Test
làm doanh thu hay dữ liệu thật trong báo cáo.

## Giới hạn của kết luận

Các test tự động không thể thay thế thử nghiệm SQL Server, camera và dữ liệu
thật. Không có tuyên bố accuracy của model, không cam kết OCR chính xác tuyệt đối,
không cam kết Gemini trong Chatbot không bao giờ suy luận sai. Những trường hợp chưa có dữ liệu
hoặc cấu hình sẽ hiện thông báo và giữ phần nghiệp vụ tiếp tục hoạt động.
