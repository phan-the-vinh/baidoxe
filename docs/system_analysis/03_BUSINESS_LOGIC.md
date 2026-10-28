# QUY TẮC NGHIỆP VỤ VÀ THUẬT TOÁN (BUSINESS LOGIC)

> **Dự án:** Hệ thống Quản lý Bãi Đỗ Ô Tô Tích Hợp AI (Parking AI)  
> **Nguồn đối chiếu:** Đặc tả V1.1 (UC001–UC020), `docs/NGHIEP_VU.md`, `app/services/parking.py`, `app/services/billing.py`  
> **Trạng thái:** Đã xác minh qua mã nguồn và test suite (100% khớp logic)  

---

## 1. Các Thực thể Nghiệp vụ và Vòng đời Trạng thái

### 1.1. Vòng đời Lượt Gửi Xe (Visit Lifecycle)
1. **Khởi tạo (`CHECK_IN`):**
   - Xe tới cổng, nhân viên kiểm tra biển số và chọn ô đỗ còn trống (`EMPTY`).
   - Tạo bản ghi `visits` với mã vé ngẫu nhiên duy nhất `V<16 hex chars>` (ví dụ: `V3A7F9B1C2D4E5F6`).
   - Trạng thái: `exited_at IS NULL`.
   - Trạng thái ô đỗ chuyển thành `OCCUPIED`.
2. **Chuyển ô đỗ trong bãi (`MOVE_SLOT`):**
   - Khi xe di chuyển thực tế hoặc nhân viên sắp xếp lại: nhân viên thực hiện đổi ô trên giao diện.
   - Ô cũ chuyển thành `EMPTY`, ô mới chuyển thành `OCCUPIED`. Lượt gửi cập nhật `slot_code` mới.
   - Toàn bộ hành vi được ghi nhận vào `audit_logs`.
3. **Báo mất vé (`LOST_TICKET`):**
   - Nhân viên tra cứu theo biển số xe hoặc thời gian vào bãi.
   - Đánh dấu cờ `lost_ticket = True` và bắt buộc nhập ghi chú xác minh nhân thân (`verification_note`).
   - **Quy tắc tuyệt đối:** Không được thu thêm bất kỳ khoản phụ thu phạt mất vé nào ngoài tiền gửi xe thực tế tính theo thời gian.
4. **Kết thúc lượt gửi (`CHECK_OUT`):**
   - Tính toán cước phí theo thời gian thực tế `exited_at`.
   - Tạo bản ghi `payments` (phương thức tiền mặt `TURN`), gán thu ngân `cashier_id`.
   - Cập nhật `visits.exited_at = now`, `visits.amount = total`, `visits.exited_by = cashier_id`.
   - Giải phóng ô đỗ: `slots.state = 'EMPTY'`.

### 1.2. Vòng đời Ô đỗ (Parking Slot Lifecycle)
- Trạng thái nghiệp vụ: `EMPTY` (Trống) hoặc `OCCUPIED` (Đang có xe).
- Cờ kiểm tra: `needs_review` (True/False).
- Khi AI phát hiện mâu thuẫn (ví dụ: camera thấy có xe nhưng CSDL ghi nhận trống):
  - Ô bị bật cờ `needs_review = True`.
  - Sinh bản ghi cảnh báo `alerts` với lý do cụ thể.
  - **Ô có cờ `needs_review = True` sẽ bị chặn không cho gán xe mới** cho đến khi Quản lý/Nhân viên nhấn "Xác nhận ô" hoặc giải quyết cảnh báo (`resolve_alert`).

---

## 2. Thuật toán Tính Phí Gửi Xe (Billing Engine)

Thuật toán được cài đặt tại `app/services/billing.py:calculate_fee()`.

### 2.1. Cấu hình Bảng giá Mặc định (Demo Rate)
- **Giá vé lượt (`turn_fee`):** 20.000 VNĐ cho mỗi block tối đa 2 giờ (7.200 giây).
- **Giá qua đêm (`night_fee`):** 80.000 VNĐ cho mỗi đêm.
- **Giá vé tháng (`monthly_fee`):** Quản lý cấu hình linh hoạt (khi seed để `NULL`).

### 2.2. Khung giờ Ban Đêm và Điều kiện Qua Đêm
- **Khung ban đêm:** Từ `22:00:00` hôm trước đến `08:00:00` sáng hôm sau (tổng độ dài khung là 10 giờ = 36.000 giây) theo giờ Việt Nam (`Asia/Ho_Chi_Minh`).
- **Điều kiện hưởng giá qua đêm:** Thời gian xe gửi giao với khung đêm phải **đạt ít nhất 5 giờ (18.000 giây)**.
  - Nếu thời gian trong khung đêm $\ge 18.000$ giây: Tính đúng 1 phí qua đêm (`80.000đ`). Toàn bộ thời gian nằm trong khung đêm đó không bị tính thêm phí lượt.
  - Nếu thời gian trong khung đêm $< 18.000$ giây: Không đủ điều kiện qua đêm. Toàn bộ số giây này được chuyển ra ngoài để tính chung vào thời gian vé lượt.
- **Gửi nhiều đêm liên tiếp:** Mỗi đêm được xét hoàn toàn độc lập. Lượt gửi kéo dài nhiều ngày qua đêm nào đủ điều kiện thì tính phí đêm đó.

### 2.3. Quy tắc Tính Thời gian Lẻ Ban Ngày (Daytime Block Rounding)
1. Lấy tổng thời gian gửi (tính bằng giây) trừ đi tất cả các khoảng thời gian đêm đã tính phí qua đêm đủ điều kiện.
2. Số giây còn lại được chia cho thời lượng 1 lượt (2 giờ = 7.200 giây).
3. **Làm tròn lên:** Nếu còn dư dù chỉ 1 giây lẻ ngoài các block 7.200 giây, hệ thống làm tròn lên thêm 1 lượt đầy đủ:
   $$\text{Số lượt} = \left\lceil \frac{\text{Tổng giây ngoài đêm}}{7200} \right\rceil$$
4. **Trường hợp vào và ra cùng thời điểm ($entered\_at = exited\_at$):** Vẫn tính tối thiểu 1 lượt (20.000 VNĐ), hệ thống không áp dụng thời gian ân hạn miễn phí (No Grace Period).

### 2.4. Bảng Tra cứu Kiểm thử Biên (Boundary Test Cases)

Bảng đối chiếu chuẩn từ `tests/test_billing.py`:

| Thời điểm vào | Thời điểm ra | Phân tích thời gian | Tiền tính toán | Ghi chú nghiệp vụ |
|---|---|---|---|---|
| 09:00:00 | 09:00:00 | 0 giây | 20.000đ | Tối thiểu 1 lượt |
| 09:00:00 | 11:00:00 | Đúng 2 giờ (7.200s) | 20.000đ | Tròn 1 lượt |
| 09:00:00 | 11:00:01 | 2 giờ 1 giây | 40.000đ | Vượt 1 giây, làm tròn 2 lượt |
| 09:00:00 | 13:00:00 | Đúng 4 giờ | 40.000đ | Đúng 2 lượt |
| 22:00:00 | 02:59:59 (hôm sau) | 4 giờ 59 phút 59 giây | 60.000đ | Chưa đủ 5h đêm -> tính 3 lượt ngày |
| 22:00:00 | 03:00:00 (hôm sau) | Đúng 5 giờ ban đêm | 80.000đ | Đủ 5h đêm -> tính đúng 1 đêm |
| 22:00:00 | 08:00:00 (hôm sau) | Trọn khung đêm (10 giờ) | 80.000đ | Tính đúng 1 đêm |
| 03:00:00 | 08:00:00 (cùng ngày) | Đúng 5 giờ ban đêm | 80.000đ | Đủ 5h đêm -> tính đúng 1 đêm |
| 03:00:01 | 08:00:00 (cùng ngày) | 4 giờ 59 phút 59 giây | 60.000đ | Thiếu 1 giây -> tính 3 lượt ngày |
| 21:00:00 | 09:00:00 (hôm sau) | 1 đêm đủ + 2h ban ngày | 100.000đ | 1 đêm (80k) + 1 lượt ngày (20k) |
| 21:00:00 | 09:00:01 (hôm sau) | 1 đêm đủ + 2h 1s ban ngày | 120.000đ | 1 đêm (80k) + 2 lượt ngày (40k) |
| 22:00 (N1) | 08:00 (N3) | 2 đêm đủ + 14h ngày | 300.000đ | 2 đêm (160k) + 7 lượt ngày (140k) |

---

## 3. Quy tắc Nghiệp vụ Vé Tháng (Monthly Passes)

- **Một biển số - Một hồ sơ:** Mỗi biển số ô tô chỉ có duy nhất 1 hồ sơ vé tháng (`monthly_passes.plate UNIQUE`).
- **Gia hạn độc lập theo kỳ (`pass_periods`):**
  - Vé tháng không sửa đổi kỳ cũ mà luôn thêm mới kỳ gia hạn tiếp theo.
  - Mỗi kỳ có `start_date`, `end_date`, và số tiền đã thu (`amount`).
  - Kỳ mới bắt buộc phải có ngày bắt đầu nối tiếp hoặc sau ngày kết thúc của kỳ trước (`start_date >= previous.end_date + 1`).
- **Khoảng nghỉ không miễn phí:** Nếu khách không gia hạn liên tục, khoảng thời gian ngắt quãng giữa 2 kỳ vé tháng sẽ không được hưởng miễn phí; xe gửi trong khoảng đó sẽ bị tính vé lượt.
- **Quyền lợi miễn phí theo thời điểm xe vào:**
  - Nếu tại thời điểm xe vào bãi (`entered_at`), biển số xe thuộc một kỳ vé tháng đang có hiệu lực -> Lượt gửi được gắn cờ `ticket_type = 'MONTH'` và `monthly_period_id`.
  - **Miễn phí trọn lượt gửi:** Kể cả khi kỳ vé tháng hết hạn trong lúc xe đang nằm trong bãi, lượt gửi đó vẫn được miễn phí hoàn toàn khi ra (`total = 0`), tuyệt đối không truy thu hồi tố phần thời gian vượt hạn.
- **Mua vé tháng khi xe đang gửi vé lượt:** Không áp dụng hồi tố đổi hình thức của lượt gửi hiện tại. Kỳ vé tháng mới chỉ áp dụng cho lần vào bãi tiếp theo.

---

## 4. Quản lý Phiên bản Bảng giá (Rate Versioning)

- Bảng `rates` được thiết kế theo mô hình **Append-Only** (chỉ chèn thêm, không sửa xóa).
- Khi Quản lý cập nhật bảng giá mới:
  - Hệ thống tạo 1 bản ghi `Rate` mới với ID tự tăng và thời điểm tạo `created_at`.
  - **Bảo toàn chi phí lượt đang gửi:** Lượt gửi đã phát sinh trước đó liên kết trực tiếp với `rate_id` tại thời điểm xe vào. Việc đổi bảng giá không làm thay đổi cước phí của những xe đã vào bãi trước đó.

---

## 5. Quy tắc Kiểm toán & Chống Gian lận (Audit & Integrity)

- Mọi thao tác làm thay đổi dữ liệu (Check-in, Check-out, Đổi vị trí, Sửa nhân viên, Đổi bảng giá, Tạo vé tháng, Xử lý cảnh báo, Sao lưu) bắt buộc phải ghi 1 bản ghi vào bảng `audit_logs` trong cùng transaction.
- Nội dung `detail` trong `audit_logs` lưu dưới dạng JSON mô tả ngữ cảnh, tuyệt đối **không lưu mật khẩu băm, chuỗi kết nối hoặc API key**.
