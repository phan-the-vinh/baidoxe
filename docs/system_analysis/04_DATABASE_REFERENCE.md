# THAM CHIẾU CƠ SỞ DỮ LIỆU VÀ TÍNH TOÀN VẸN (DATABASE REFERENCE)

> **Hệ quản trị CSDL:** Microsoft SQL Server (Transact-SQL)  
> **Driver kết nối:** Microsoft ODBC Driver 18 for SQL Server (`mssql+pyodbc`)  
> **ORM Layer:** SQLAlchemy 2.0.43 / Flask-SQLAlchemy 3.1.1  
> **Tên Database:** `ParkingAI` (Cấu hình qua biến môi trường `DB_NAME`)  
> **Nguồn đối chiếu:** `database/schema.sql` và `app/models.py`  

---

## 1. Danh mục 13 Bảng Dữ liệu Thực tế

| Tên Bảng (Table) | Mục đích lưu trữ & Vai trò trong hệ thống | Số cột | Khóa chính (PK) |
|---|---|:---:|---|
| `areas` | Danh mục khu đỗ xe (A, B, D). Ràng buộc cố định mã khu. | 3 | `code` (VARCHAR(1)) |
| `slots` | Danh mục 20–24 vị trí đỗ xe, trạng thái đỗ và kết quả AI gần nhất. | 8 | `code` (VARCHAR(3)) |
| `parking_capacity_history` | Lịch sử thay đổi sức chứa bãi xe phục vụ tính toán tỷ lệ lấp đầy quá khứ. | 3 | `effective_at` (DATETIME2(6)) |
| `monthly_passes` | Danh mục hồ sơ vé tháng ô tô theo biển số xe. | 5 | `id` (INT IDENTITY) |
| `pass_periods` | Lịch sử các chu kỳ gia hạn và số tiền thu của từng vé tháng. | 5 | `id` (INT IDENTITY) |
| `rates` | Các phiên bản bảng giá dịch vụ (lượt, đêm, tháng). Thiết kế Append-Only. | 6 | `id` (INT IDENTITY) |
| `visits` | Thực thể trung tâm: Nhật ký các lượt xe vào và ra bãi đỗ. | 15 | `id` (INT IDENTITY) |
| `payments` | Các giao dịch thanh toán tiền mặt thực tế đã xác nhận thu. | 7 | `id` (INT IDENTITY) |
| `alerts` | Cảnh báo sai lệch vị trí đỗ hoặc mâu thuẫn giữa AI và thực tế. | 7 | `id` (INT IDENTITY) |
| `observations` | Nhật ký thô các lần suy đoán AI (Inference logs) theo từng ô đỗ. | 7 | `id` (INT IDENTITY) |
| `audit_logs` | Nhật ký kiểm toán mọi thao tác thay đổi dữ liệu của nhân viên/quản lý. | 5 | `id` (INT IDENTITY) |
| `backup_runs` | Lịch sử thực thi và xác minh các bản sao lưu native `.bak`. | 5 | `id` (INT IDENTITY) |
| `site_lock` | Bảng điều phối khóa bi quan toàn cục mức hệ thống (Pessimistic Lock). | 2 | `id` (INT) |

---

## 2. Chi tiết Cấu trúc Bảng, Ràng buộc và Chỉ mục (Schema & Constraints)

### 2.1. Bảng `site_lock` (Concurrency Coordinator)
- **Cấu trúc:** `id` (INT PK), `revision` (INT NOT NULL DEFAULT 0).
- **Cơ chế:** Luôn chỉ có đúng 1 bản ghi (`id = 1`). Mọi transaction ghi đều thực hiện `SELECT SiteLock WHERE id=1 WITH (UPDLOCK, HOLDLOCK)`.

### 2.2. Bảng `areas` & `slots` (Topology)
- `areas`:
  - `code` VARCHAR(1) PK: `CHECK (code IN ('A','B','D'))` (Tên constraint: `ck_fixed_area`).
  - `name` NVARCHAR(60) NOT NULL.
  - `description` NVARCHAR(500) NOT NULL.
- `slots`:
  - `code` VARCHAR(3) PK: Ví dụ `'A01'`, `'B10'`, `'D24'`.
  - `area_code` VARCHAR(1) NOT NULL FK -> `areas(code)`.
  - `state` VARCHAR(8) NOT NULL: `CHECK (state IN ('EMPTY','OCCUPIED'))` (`ck_slot_state`).
  - `needs_review` BIT NOT NULL DEFAULT 0.
  - `changed_at` DATETIME2(6) NOT NULL.
  - `ai_state` VARCHAR(8) NULL, `ai_confidence` FLOAT NULL, `ai_at` DATETIME2(6) NULL.
  - **Ràng buộc DDL Cố định (`ck_fixed_slot`):**
    ```sql
    CHECK (
      (area_code='A' AND code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10')) OR
      (area_code='B' AND code IN ('B01','B02','B03','B04','B05','B06','B07','B08','B09','B10')) OR
      (area_code='D' AND code IN ('D01','D02','D03','D04','D05','D06','D07','D08','D09','D10',
                                  'D11','D12','D13','D14','D15','D16','D17','D18','D19','D20',
                                  'D21','D22','D23','D24'))
    )
    ```

### 2.3. Bảng `visits` (Core Transaction Entity)
- `id` INT IDENTITY PK.
- `ticket_code` VARCHAR(32) NOT NULL UNIQUE (Mã vé dạng `V...`).
- `plate` VARCHAR(16) NOT NULL (Biển số xe đã chuẩn hóa không dấu chấm/gạch).
- `slot_code` VARCHAR(3) NOT NULL FK -> `slots(code)`.
- `entered_at` DATETIME2(6) NOT NULL, `exited_at` DATETIME2(6) NULL.
- `ticket_type` VARCHAR(8) NOT NULL: `CHECK (ticket_type IN ('TURN','MONTH'))`.
- `monthly_period_id` INT NULL FK -> `pass_periods(id)`.
- `rate_id` INT NOT NULL FK -> `rates(id)`.
- `created_by` INT NOT NULL FK -> `users(id)`, `exited_by` INT NULL FK -> `users(id)`.
- `amount` INT NULL: `CHECK (amount IS NULL OR amount>=0)`.
- `fee_breakdown` NTEXT NULL (Lưu JSON chi tiết các block giờ và phụ thu đêm).
- `lost_ticket` BIT NOT NULL DEFAULT 0.
- `verification_note` NVARCHAR(500) NULL.
- **Ràng buộc logic thời gian:** `CHECK (exited_at IS NULL OR exited_at >= entered_at)`.
- **Filtered Unique Indexes cực kỳ quan trọng:**
  ```sql
  -- Chống 1 xe tạo 2 lượt đang gửi cùng lúc:
  CREATE UNIQUE INDEX uq_active_plate ON visits (plate) WHERE exited_at IS NULL;
  
  -- Chống 2 xe chiếm cùng 1 ô đỗ cùng lúc:
  CREATE UNIQUE INDEX uq_active_slot ON visits (slot_code) WHERE exited_at IS NULL;
  ```

### 2.4. Bảng `payments` (Financial Integrity)
- `id` INT IDENTITY PK.
- `visit_id` INT NULL FK -> `visits(id)`, `period_id` INT NULL FK -> `pass_periods(id)`.
- `amount` INT NOT NULL: `CHECK (amount >= 0)`.
- `kind` VARCHAR(8) NOT NULL (`'TURN'` hoặc `'MONTH'`).
- `paid_at` DATETIME2(6) NOT NULL, `cashier_id` INT NOT NULL FK -> `users(id)`.
- **Ràng buộc nguồn thu tiền (`ck_payment_source`):**
  ```sql
  CHECK (
    (kind='TURN' AND visit_id IS NOT NULL AND period_id IS NULL) OR
    (kind='MONTH' AND period_id IS NOT NULL AND visit_id IS NULL)
  )
  ```
- **Chống thanh toán 2 lần (Filtered Unique Indexes):**
  ```sql
  CREATE UNIQUE INDEX uq_payment_visit ON payments (visit_id) WHERE visit_id IS NOT NULL;
  CREATE UNIQUE INDEX uq_payment_period ON payments (period_id) WHERE period_id IS NOT NULL;
  ```

### 2.5. Bảng `alerts` (Anomaly Management)
- `id` INT IDENTITY PK.
- `slot_code` VARCHAR(3) NOT NULL FK -> `slots(code)`.
- `reason` NVARCHAR(250) NOT NULL.
- `created_at` DATETIME2(6) NOT NULL.
- `resolved_at` DATETIME2(6) NULL, `resolved_by` INT NULL FK -> `users(id)`, `resolution` NVARCHAR(500) NULL.
- **Filtered Unique Index:**
  ```sql
  -- Một ô đỗ chỉ có tối đa 1 cảnh báo chưa xử lý:
  CREATE UNIQUE INDEX uq_open_alert ON alerts (slot_code) WHERE resolved_at IS NULL;
  ```

---

## 3. Cơ chế Khóa và Xử lý Giao dịch (Transaction & Concurrency Control)

### 3.1. Phân tích hàm `write_lock()` trong `app/services/common.py`
```python
def write_lock():
    query = select(SiteLock).where(SiteLock.id == 1)
    if db.engine.dialect.name == 'mssql':
        query = query.with_hint(SiteLock, 'WITH (UPDLOCK, HOLDLOCK)', dialect_name='mssql')
    lock = db.session.execute(query.execution_options(populate_existing=True)).scalar_one()
    lock.revision += 1
    db.session.flush()
```
- **UPDLOCK:** Yêu cầu khóa cập nhật ngay từ đầu, ngăn chặn các transaction khác cũng đòi lấy UPDLOCK (chống Deadlock kiểu đọc-rồi-ghi đồng thời).
- **HOLDLOCK:** Giữ khóa này cho đến khi kết thúc toàn bộ transaction (tương đương cấp độ cô lập `SERIALIZABLE` cho dòng dữ liệu này).
- **Đánh giá kiến trúc:**
  - *Ưu điểm:* Giải quyết triệt để 100% tình trạng Race Condition, Double-Booking ô đỗ hoặc tạo trùng lượt gửi đối với bãi xe tập trung quy mô nhỏ.
  - *Nhược điểm:* Đây là cơ chế **Coarse-Grained Locking**. Toàn bộ các thao tác ghi trên hệ thống bị biến thành hàng đợi đơn luồng (Single-threaded). Không thể mở rộng cho hệ thống bãi xe nhiều cổng ra/vào với lưu lượng lớn.

### 3.2. Ngoại lệ Giao dịch Sao lưu (`app/services/backup.py`)
- Lệnh `BACKUP DATABASE` và `RESTORE VERIFYONLY` của SQL Server **không được phép chạy bên trong transaction người dùng**.
- Do đó, mã nguồn chủ động tách riêng một connection với chế độ `AUTOCOMMIT`:
  ```python
  with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
      cursor = connection.connection.driver_connection.cursor()
      cursor.execute(f'BACKUP DATABASE [{name}] TO DISK = ? WITH COPY_ONLY, CHECKSUM', destination)
      cursor.execute('RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM', destination)
  ```

---

## 4. Các Nguy cơ Toàn vẹn Dữ liệu Tiềm ẩn (Integrity Risks)

1. **Phình to bảng `observations`:** Mỗi lần camera phân tích hoặc người dùng gửi frame, kết quả của từng ô đỗ được ghi vào bảng này. Với bãi đỗ 24 ô, nếu gửi frame mỗi 3 giây thì mỗi phút sinh $24 \times 20 = 480$ bản ghi, tương đương gần 700.000 bản ghi/ngày. Bảng này chưa có chỉ mục theo `captured_at` và chưa có cơ chế xóa định kỳ.
2. **Khóa cứng mã ô ở DDL Constraint:** Việc đổi bãi đỗ từ 20 ô (A/B) sang 24 ô (D) đòi hỏi phải chạy script can thiệp DDL mức admin (`migrate_parking_slots_ab_to_d01_d24_draft_not_executed.sql`), không thể cấu hình động qua API hay giao diện.
