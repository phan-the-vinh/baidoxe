# Kế hoạch kiểm thử migration A/B sang D01-D24

Trạng thái: **bản nháp, chưa thực thi**.

## Dữ liệu thực tế tại thời điểm khảo sát

- `areas`: 2 bản ghi (`A`, `B`).
- `slots`: 20 bản ghi (`A01-A10`, `B01-B10`).
- `visits`: 4 bản ghi, đều đã hoàn tất; hiện không có lượt đang hoạt động.
- `visits` từng dùng `B02`: 2 bản ghi, đều đã hoàn tất.
- `observations`: 0; `alerts`: 0.
- `payments`: 5, gồm 4 thanh toán lượt gắn với `visits` và 1 thanh toán vé tháng.
- `audit_logs`: 10; có 6 bản ghi chứa mã ô A/B trong JSON lịch sử.
- Không có tham chiếu mồ côi và không có trigger trên các bảng migration.

Sai lệch cần xác nhận: yêu cầu nhắc một lượt đang hoạt động tại `B02`, nhưng dữ
liệu hiện tại không có lượt đang hoạt động. Script vẫn kiểm đếm tại thời điểm chạy
và bảo toàn mọi lượt `B02` đang hoạt động thành `D12` nếu dữ liệu thay đổi trước
maintenance window.

## Điều kiện trước khi chạy

1. Có phê duyệt riêng cho việc thực thi migration.
2. Dừng Flask và mọi tiến trình có thể ghi `ParkingAI`.
3. Xác nhận `MSSQLSERVER` Running, `ParkingAI` ONLINE và không có lỗi I/O mới.
4. Chạy lại thống kê số bản ghi và kiểm tra orphan/duplicate trong script.
5. Xác nhận backup sau vẫn tồn tại và hợp lệ:
   `C:\SQLBackups\ParkingAI_FULL_COPY_ONLY_CHECKSUM_20260924_022216_342.bak`.
6. Chỉ khi đạt toàn bộ điều kiện trên mới đổi `@ExecuteMigration` từ `0` thành `1`.

## Kiểm tra SQL bắt buộc

- Chỉ còn một khu `D` và đúng 24 ô `D01-D24`.
- `A01-A10 -> D01-D10`, `B01-B10 -> D11-D20`; `D21-D24` mới là `EMPTY`.
- Số lượng `visits`, `observations`, `alerts`, `payments`, `audit_logs` không đổi.
- ID lượt gửi, vé, biển số, thời gian, số tiền, liên kết thanh toán không đổi.
- Mọi `slot_code` trong ba bảng con được đổi đúng theo ánh xạ, không orphan.
- Trạng thái và dữ liệu AI của 20 ô cũ không đổi; chỉ mã và khu được đổi.
- Nội dung `audit_logs.detail` giữ nguyên, nên lịch sử cũ vẫn có thể chứa A/B.
- Các FK mới được tạo `WITH CHECK`; các unique index lượt đang gửi vẫn tồn tại.
- Nếu bất kỳ kiểm tra nào thất bại, `XACT_ABORT` và `TRY/CATCH` rollback toàn bộ.

## Kiểm thử ứng dụng sau migration (chỉ thực hiện sau khi mã nguồn tương thích)

1. Đăng nhập và mở dashboard, `/slots`, `/entry`, `/exit`, `/visits`, `/statistics`.
2. Xác nhận giao diện chỉ hiển thị `D01-D24`, tổng sức chứa 24.
3. Tra cứu bốn lượt cũ và đối chiếu vị trí mới, thời gian, phí, thanh toán.
4. Tạo một lượt thử ở ô trống D21; kiểm tra chống trùng biển số và chống trùng ô.
5. Chuyển lượt giữa hai ô D, sau đó checkout và kiểm tra thanh toán/audit mới.
6. Xác nhận `/vision` CNN cũ bị chặn ghi trạng thái cho tới khi có cấu hình D01-D24.
7. Xác nhận `/ai-demo` vẫn chỉ phát lại dữ liệu demo và không ghi `ParkingAI`.

## Thay đổi mã nguồn cần chuẩn bị trước khi chạy SQL

- Schema/seed: `app/models.py`, `app/cli.py`, `database/schema.sql`.
- Dung lượng/thống kê: `app/services/analytics.py`.
- API và đếm theo khu: `app/routes.py`, `app/static/js/app.js`.
- Giao diện: `app/templates/base.html`, `login.html`, `dashboard.html`,
  `macros.html`, `slots.html`, `areas.html`, `entry.html`.
- Cảnh báo ranh giới demo/nghiệp vụ: `app/templates/ai_demo.html`,
  `app/templates/vision.html`, `app/static/js/vision_video.js`.
- CNN 20 ROI cũ: chặn ghi trong `app/services/vision.py`; nếu tiếp tục dùng CNN thì
  phải chuẩn bị riêng `ai/roi.py`, `ai/predict_parking.py`,
  `tools/parking_roi_editor.py`, `tools/crop_slots.py` và model/ROI 24 ô đã kiểm thử.
- Kiểm thử/tài liệu: `tests/test_business.py`, `tests/test_ai_data.py`,
  các test migration mới và `README.md`.

## Phương án dự phòng không được tự thực hiện

Không cần dùng phương án xóa dữ liệu vì migration trực tiếp có thể bảo toàn dữ liệu.
Nếu buộc phải tạo lại cấu hình và nghiệp vụ tại snapshot hiện tại, tối thiểu sẽ phải
xử lý/xóa theo thứ tự: 4 `payments` loại TURN, 4 `visits`, 0 `alerts`,
0 `observations`, 20 `slots`, 2 `areas`. Có thể giữ nguyên dữ liệu vé tháng,
1 payment MONTH, bảng giá, người dùng và 10 audit log, nhưng audit sẽ tham chiếu
nghiệp vụ đã mất. Phương án này làm mất lịch sử và không được khuyến nghị; cần một
phê duyệt và backup mới riêng nếu số liệu thay đổi.

## Phục hồi

- Lỗi trước `COMMIT`: script tự rollback.
- Lỗi phát hiện sau `COMMIT`: dừng ứng dụng; không chạy script đảo chiều tùy tiện.
  Khôi phục từ `.bak` chỉ được thực hiện theo một phê duyệt RESTORE riêng và nên
  kiểm thử restore sang database khác trước.
