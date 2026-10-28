# Chuẩn bị dataset bãi xe thật

Thư mục gốc `dataset/parking` gồm ba tập `train`, `validation`, `test`.
Trong mỗi tập có đúng hai thư mục nhãn `empty` và `occupied`.
Ảnh đầu vào phải là **ảnh một ô đỗ**, không phải ảnh toàn bãi.

## Thu thập và gán nhãn

1. Chọn camera cố định quan sát bãi thực hoặc dữ liệu được phép sử dụng.
2. Chụp các phiên sáng, trưa, chiều, ban đêm; có/không bóng đổ, các màu xe khác nhau.
3. Đánh dấu polygon ROI bằng `tools/parking_roi_editor.py` trên ảnh toàn bãi.
4. Dùng `tools/crop_slots.py` để cắt ảnh theo cùng pipeline inference.
5. Nhìn từng ảnh để gán nhãn. `empty`: ô không có ô tô; `occupied`: có ô tô trong ô.
6. Ảnh che khuất đến mức không kết luận được: đưa ra ngoài hai lớp, không gán đoán.
7. Ghi rõ định nghĩa nhãn và cách xử lý xe đè vạch trong nhật ký nhóm.
   Nếu xe chiếm hai ô, cả hai ROI có phần xe đủ rõ được gán occupied, nhưng cần ghi
   quy tắc nhất quán; CNN không tự xác định xe thuộc lượt gửi nào.

Nên thu vài trăm ảnh trở lên mỗi lớp nếu điều kiện cho phép, trải đều nhiều phiên.
Đây là mục tiêu thu thập, **không phải bảo đảm độ chính xác**. Nếu chỉ vài ảnh,
có thể chạy pipeline nhưng không được diễn giải thành chất lượng triển khai thực tế.

## Chia dữ liệu

Khuyến nghị khoảng 70% train, 15% validation, 15% test **theo nhóm phiên chụp**.
Vì giữ nguyên nhóm nên tỷ lệ thực tế có thể khác. Các ô cắt từ cùng một khung,
các khung liên tiếp hoặc rất gần nhau phải thuộc cùng một nhóm.

Ví dụ file `dataset/source.csv` (chỉ là định dạng, không phải dữ liệu đã tồn tại):

```csv
path,label,group
unlabeled/camera1_20260901_sang/frame001_A01.png,empty,camera1_20260901_sang
unlabeled/camera1_20260901_sang/frame001_A02.png,occupied,camera1_20260901_sang
unlabeled/camera1_20260902_toi/frame001_A01.png,occupied,camera1_20260902_toi
```

Đường dẫn tương đối tính từ vị trí file CSV. Công cụ yêu cầu ít nhất 6 nhóm;
thực tế nên thu nhiều hơn để có test đại diện. Nếu muốn đánh giá tổng quát qua
camera, tách toàn bộ camera kiểm thử ra khỏi các tập còn lại.

```powershell
python tools/prepare_dataset.py --manifest dataset/source.csv --output dataset/parking
```

Không chạy công cụ chia vào thư mục đã có ảnh. Nếu đã chia thủ công, giữ dữ liệu
ở đúng cấu trúc và chạy script train trực tiếp. Script kiểm tra ảnh lỗi, đủ nhãn,
và ảnh giống hệt (SHA-256) xuất hiện ở nhiều tập. Hash không phát hiện được mọi ảnh
gần giống: nhóm vẫn phải quản lý nguồn/phiên chụp để tránh rò rỉ.

## Augmentation và xử lý ảnh

Train sử dụng lật ngang, xoay nhẹ, zoom nhẹ, đổi tương phản/độ sáng trong Keras.
Không lật dọc hay xoay lớn làm sai ngữ cảnh. Không augmentation validation/test.
Phần bên ngoài polygon được làm đen, giống inference. Resize và chuyển RGB trước
đưa vào model; Rescaling nằm trong model.

## Minh chứng báo cáo

Ghi số ảnh mỗi nhãn/mỗi tập, nguồn và quyền sử dụng, ngày/camera, quy tắc chia.
Lưu lại `dataset_manifest.json`, `run_config.json`, model summary, history và model.
Chọn model theo validation; đánh giá cuối trên test giữ riêng. Báo cáo thêm ảnh
false positive (ô trống bị báo có xe) và false negative (ô có xe bị báo trống).

Không trộn ảnh tổng hợp kiểm thử pipeline với test thật để công bố chất lượng.
Không đặt tên một file pretrained thành `parking_classifier.keras` rồi gọi là
model tự huấn luyện. Không có ảnh hoặc chưa evaluate thì không ghi accuracy/F1.
