# Quy tắc nghiệp vụ và điểm cần nhóm rà soát

Nguồn: đặc tả V1.1 đính kèm, UC001–UC020. Prompt người dùng dừng ở phần mở ảnh
trong công cụ ROI; các thao tác click 4 điểm/lưu/hủy là thiết kế công cụ hỗ trợ,
không phải thay đổi nghiệp vụ quản lý bãi xe.

## Các quy tắc giữ nguyên

- Một bãi, ô tô, hai khu A/B, tổng 20 vị trí; không thêm/xóa khu/ô.
- Hai vai trò Người quản lý và Người dùng; không khách hàng đăng nhập.
- Người dùng mới chỉ có role STAFF; không có API cấp vai trò Người quản lý qua giao diện.
- Xe vào phải có biển số và ô thực tế được xác nhận; một xe/một ô chỉ một lượt đang gửi.
- Thanh toán tiền mặt; vé lượt cần xác nhận đã thu; đã loại bỏ chức năng vé tháng và luồng mất vé.
- Giá demo 20.000đ/lượt tối đa 2 giờ; qua đêm 80.000đ, khung 22:00–08:00 đủ 5 giờ.
- Bảng giá mới không thay phí của xe đã vào trước khi đổi giá.
- AI không tự ghi đè trạng thái đang lưu khi mâu thuẫn hoặc điểm tin cậy thấp.
- OCR chỉ gợi ý và cần người dùng xác nhận. AI Chatbot chỉ Người quản lý sử dụng.
- Thông tin người dùng chỉ thêm/sửa, không khóa hoặc xóa.

## Các chi tiết đặc tả chưa chốt và cách triển khai được công khai

| Điểm cần cụ thể hóa | Quyết định trong mã nguồn |
|---|---|
| Cộng thời gian lẻ trước/sau đêm | Cộng toàn bộ giây ngoài các khung đêm đủ điều kiện rồi làm tròn lên mỗi 7.200 giây; không làm tròn từng đoạn riêng |
| Gửi nhiều đêm | Xét mỗi đêm độc lập. Đêm nào giao với lượt gửi ít nhất 18.000 giây thì tính 1 phí qua đêm cho phần giao đó |
| Xe vào và ra cùng thời điểm | Vé lượt vẫn tối thiểu một lượt, phù hợp không ân hạn |
| Vé tháng hết hạn giữa lượt gửi | Chốt quyền hưởng vé tháng lúc xe vào, miễn phí trọn lượt đó. Không truy thu hồi tố |
| Mua/gia hạn vé tháng khi xe đang gửi vé lượt | Không đổi hình thức của lượt đã phát sinh; kỳ vé áp dụng cho lần vào đủ điều kiện tiếp theo |
| Thời hạn vé tháng | Ngày đầu/ngày cuối đều tính, giờ Việt Nam. Giao diện gợi ý 30 ngày nhưng Người quản lý quyết định và nhập mức thu cho kỳ |
| Giá tháng chưa có số cụ thể | Để NULL khi seed; không tự chọn con số. Người quản lý phải nhập giá gợi ý hoặc số tiền thu thực tế |
| Hồ sơ vé tháng cũ hết hạn | Một hồ sơ cho một biển số; dùng Gia hạn để tạo kỳ mới và giữ lịch sử. Không có hai hồ sơ cùng biển số |
| Gia hạn cách quãng | Chỉ kỳ đã ghi nhận được xem là còn hiệu lực; khoảng nghỉ không miễn phí |
| Quyền Người quản lý hỗ trợ vận hành | Người quản lý có thể thao tác xe vào/ra, xử lý vị trí và OCR để hỗ trợ Người dùng. Các chức năng quản trị vẫn chặn STAFF ở backend |
| Gợi ý ô đỗ | Ô EMPTY, không có cảnh báo chưa xử lý; ưu tiên theo A01…B10 |
| AI có xe nhưng chưa có lượt gửi | Không tự tạo lượt gửi/biển số. Cảnh báo; người dùng có thể xác nhận OCCUPIED thủ công để chặn nhận thêm xe |
| Ô có lượt đang gửi nhưng thực tế trống | Không cho xóa liên kết bằng sửa trạng thái. Cần chuyển lượt sang ô thật hoặc làm xe ra đúng nghiệp vụ |
| AI trùng trạng thái trở lại sau cảnh báo | Cảnh báo vẫn giữ đến khi người dùng xác minh; tránh đóng cảnh báo chưa kiểm tra |
| Đối chiếu ảnh cũ | Ghi mốc nhận request trước inference. Nếu có thay đổi nghiệp vụ mới hơn hoặc kết quả AI mới hơn, bỏ qua kết quả cũ |
| Doanh thu | Vé lượt theo thời điểm xác nhận thu khi xe ra; vé tháng theo thời điểm thu tiền kỳ mới. Không tính quote, không tính hai lần |
| Lấp đầy quá khứ | Từ tổng thời gian lượt gửi chiếm ô chia 20 ô × thời gian đã trôi qua trong kỳ. Không suy ra lịch sử vị trí vật lý từ ảnh chưa có |
| Tần suất video | Lấy khung trên trình duyệt mỗi 3 giây khi bật chế độ tự nhận diện; tuần tự từng request, không hàng đợi video ngầm |

Các quyết định này được tách để nhóm/giảng viên rà soát. Không tuyên bố chúng là
câu chữ có sẵn trong đặc tả. Nếu cần đổi, sửa rule tương ứng và bổ sung test biên.

## Ví dụ tính phí với giá demo

| Vào → ra (giờ Việt Nam) | Kết quả |
|---|---|
| 09:00 → 11:00 cùng ngày | 1 lượt = 20.000đ |
| 09:00 → 11:00:01 | 2 lượt = 40.000đ |
| 22:00 → 02:59:59 hôm sau | Chưa đủ 5h ban đêm, 3 lượt = 60.000đ |
| 22:00 → 03:00 hôm sau | Đủ 5h, 1 đêm = 80.000đ |
| 21:00 → 09:00 hôm sau | 1 đêm + 2h ngoài đêm = 100.000đ |
| 21:00 → 09:00:01 hôm sau | 1 đêm + 2 lượt ngoài đêm = 120.000đ |
| 22:00 ngày 1 → 08:00 ngày 3 | 2 đêm + 14h ngày = 300.000đ |

Thời gian lưu UTC với độ chính xác microsecond; hiển thị/tính khung đêm theo
Asia/Ho_Chi_Minh. Không dùng số giờ đã làm tròn để tính tiền.

## Tính nhất quán

Xe vào/ra, thay giá, gia hạn và sửa vị trí chạy trong một transaction. Khóa chung
`site_lock` bằng `UPDLOCK, HOLDLOCK` trên SQL Server tuần tự hóa ghi cho bãi nhỏ
20 ô. Unique filtered indexes bảo vệ thêm một lượt đang gửi/biển số và một lượt/ô,
một khoản thu/lượt hoặc kỳ vé. Thất bại rollback toàn bộ thao tác.

Quote không ghi nhận doanh thu. Khi thu tiền, backend tính lại với thời gian thật;
nếu lệch số đã xem, trả 409 và yêu cầu tính lại/thu đúng. Không tin giá từ JavaScript.
