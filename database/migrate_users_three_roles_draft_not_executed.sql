-- ==============================================================================
-- DỰ ÁN PARKVIEW / PARKING AI — SCRIPT MIGRATION PHÂN QUYỀN BA VAI TRÒ (BẢN THẢO)
-- Tệp: database/migrate_users_three_roles_draft_not_executed.sql
-- Mục đích: Chuẩn hóa vai trò MANAGER -> ADMIN và cập nhật Check Constraint cho bảng users.
-- TRẠNG THÁI: BẢN THẢO — TUYỆT ĐỐI CHƯA CHẠY TRÊN DATABASE THẬT.
-- ==============================================================================

/*
================================================================================
HƯỚNG DẪN SAO LƯU VÀ KHÔI PHỤC (BẮT BUỘC TRƯỚC KHI CHẠY MIGRATION)
================================================================================

1. BƯỚC 1: SAO LƯU TOÀN PHẦN CƠ SỞ DỮ LIỆU THẬT
   Chạy lệnh T-SQL sau trên SQL Server Management Studio (SSMS) với quyền sysadmin/db_owner:

   BACKUP DATABASE [ParkingAI]
   TO DISK = N'C:\Backups\ParkingAI_Pre_RoleMigration_YYYYMMDD_HHMMSS.bak'
   WITH FORMAT, INIT, COMPRESSION, STATS = 10;
   GO

2. BƯỚC 2: XÁC MINH TÍNH TOÀN VẸN CỦA BẢN SAO LƯU
   RESTORE VERIFYONLY
   FROM DISK = N'C:\Backups\ParkingAI_Pre_RoleMigration_YYYYMMDD_HHMMSS.bak';
   GO

3. BƯỚC 3: QUY TRÌNH KHÔI PHỤC KHI CẦN THIẾT (DISASTER RECOVERY)
   Nếu xảy ra sự cố không mong muốn hoặc cần quay lại trạng thái ban đầu:
   
   USE master;
   ALTER DATABASE [ParkingAI] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
   RESTORE DATABASE [ParkingAI]
   FROM DISK = N'C:\Backups\ParkingAI_Pre_RoleMigration_YYYYMMDD_HHMMSS.bak'
   WITH REPLACE;
   ALTER DATABASE [ParkingAI] SET MULTI_USER;
   GO

LƯU Ý QUAN TRỌNG:
Transaction chỉ bảo vệ thao tác trong lúc chạy script. Nếu migration đã COMMIT
hoặc hệ thống đã ghi nhận các giao dịch xe vào/ra mới, không thể phục hồi chỉ bằng
transaction mà phải đánh giá dữ liệu trước khi quyết định khôi phục từ bản backup.
================================================================================
*/

-- -----------------------------------------------------------------------------
-- PHẦN 1: KIỂM TRA AN TOÀN TRƯỚC THI HÀNH (SAFETY PRE-CHECKS)
-- -----------------------------------------------------------------------------

-- 1.1. Khóa an toàn ngăn chạy nhầm (Phải chủ động mở cờ này khi được phê duyệt)
DECLARE @SafetyApprovalFlag BIT = 0; -- ĐẶT THÀNH 1 KHI VÀ CHỈ KHI ĐÃ CÓ PHÊ DUYỆT CHÍNH THỨC

IF @SafetyApprovalFlag = 0
BEGIN
    RAISERROR(N'CHẶN AN TOÀN: Script migration chưa được mở cờ phê duyệt (@SafetyApprovalFlag = 0). Dừng thực thi ngay lập tức.', 16, 1);
    SET NOEXEC ON;
END;
GO

-- 1.2. Xác minh đúng cơ sở dữ liệu mục tiêu ParkingAI
IF DB_NAME() <> 'ParkingAI'
BEGIN
    RAISERROR(N'LỖI CƠ SỞ DỮ LIỆU: Script chỉ được phép chạy trên database [ParkingAI]. Database hiện tại: %s', 16, 1, DB_NAME());
    RETURN;
END;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

PRINT N'=== BẮT ĐẦU QUY TRÌNH KIỂM TRA TRƯỚC MIGRATION ===';

-- 1.3. Kiểm tra sự tồn tại của bảng users
IF OBJECT_ID(N'dbo.users', N'U') IS NULL
BEGIN
    RAISERROR(N'LỖI: Bảng [dbo].[users] không tồn tại trong database ParkingAI.', 16, 1);
    RETURN;
END;

-- 1.4. Báo cáo thống kê người dùng hiện tại trước khi sửa
PRINT N'Thống kê số lượng tài khoản theo vai trò hiện tại:';
SELECT 
    [role], 
    COUNT(*) AS [UserCount]
FROM [dbo].[users]
GROUP BY [role];

-- 1.5. Tìm và hiển thị constraint kiểm tra vai trò hiện có (không giả định tên constraint)
DECLARE @CurrentConstraintName NVARCHAR(256) = NULL;
DECLARE @CurrentConstraintDef NVARCHAR(MAX) = NULL;

SELECT 
    @CurrentConstraintName = cc.name,
    @CurrentConstraintDef = cc.definition
FROM sys.check_constraints cc
INNER JOIN sys.columns c 
    ON cc.parent_object_id = c.object_id 
    AND cc.parent_column_id = c.column_id
WHERE cc.parent_object_id = OBJECT_ID(N'dbo.users')
  AND c.name = N'role';

PRINT N'Check constraint hiện có trên cột [role]: ' + ISNULL(@CurrentConstraintName, N'KHÔNG TÌM THẤY');
IF @CurrentConstraintDef IS NOT NULL
    PRINT N'Định nghĩa hiện tại: ' + @CurrentConstraintDef;

-- -----------------------------------------------------------------------------
-- PHẦN 2: THỰC HIỆN CHUYỂN ĐỔI TRONG TRANSACTION CÓ XỬ LÝ LỖI
-- -----------------------------------------------------------------------------
PRINT N'=== BẮT ĐẦU GIAO DỊCH CHUYỂN ĐỔI DỮ LIỆU ===';

BEGIN TRANSACTION;

BEGIN TRY
    -- 2.1. Xóa Check Constraint cũ nếu đang tồn tại (ngăn xung đột khi update giá trị 'ADMIN')
    IF @CurrentConstraintName IS NOT NULL
    BEGIN
        DECLARE @DropSql NVARCHAR(MAX) = N'ALTER TABLE [dbo].[users] DROP CONSTRAINT [' + @CurrentConstraintName + N'];';
        PRINT N'Thực thi: ' + @DropSql;
        EXEC sp_executesql @DropSql;
        PRINT N'Đã xóa constraint cũ thành công.';
    END;

    -- 2.2. Tạo Check Constraint mới hỗ trợ ba vai trò chính thức + chuyển tiếp
    -- Cho phép 'ADMIN', 'STAFF', 'CUSTOMER', và 'MANAGER' (để không chặn luồng cũ nếu rollback logic)
    DECLARE @AddSql NVARCHAR(MAX) = N'
        ALTER TABLE [dbo].[users] WITH CHECK 
        ADD CONSTRAINT [ck_user_role] 
        CHECK ([role] IN (''ADMIN'', ''STAFF'', ''CUSTOMER'', ''MANAGER''));
    ';
    PRINT N'Thực thi thêm constraint mới: ck_user_role';
    EXEC sp_executesql @AddSql;

    -- 2.3. Cập nhật các tài khoản MANAGER sang ADMIN
    -- GIỮ NGUYÊN: id, username, password_hash, full_name, created_at, khóa ngoại
    DECLARE @UpdatedCount INT = 0;
    
    UPDATE [dbo].[users]
    SET [role] = 'ADMIN'
    WHERE [role] = 'MANAGER';
    
    SET @UpdatedCount = @@ROWCOUNT;
    PRINT N'Số tài khoản đã chuyển từ MANAGER sang ADMIN: ' + CAST(@UpdatedCount AS NVARCHAR(10));

    -- 2.4. Kích hoạt kiểm tra toàn bộ dữ liệu hiện có theo constraint mới
    ALTER TABLE [dbo].[users] CHECK CONSTRAINT [ck_user_role];
    PRINT N'Đã kích hoạt và xác thực constraint ck_user_role thành công.';

    -- -------------------------------------------------------------------------
    -- PHẦN 3: KIỂM TRA VÀ XÁC MINH SAU MIGRATION (POST-CHECKS)
    -- -------------------------------------------------------------------------
    PRINT N'=== BÁO CÁO SAU MIGRATION (TRONG GIAO DỊCH) ===';
    
    -- Kiểm tra không có role không hợp lệ hoặc NULL
    IF EXISTS (
        SELECT 1 FROM [dbo].[users] 
        WHERE [role] NOT IN ('ADMIN', 'STAFF', 'CUSTOMER', 'MANAGER') OR [role] IS NULL
    )
    BEGIN
        RAISERROR(N'LỖI TOÀN VẸN: Tồn tại bản ghi có role không hợp lệ sau khi cập nhật!', 16, 1);
    END;

    -- Kiểm tra không có ID người dùng nào bị thay đổi hoặc mất mật khẩu
    IF EXISTS (
        SELECT 1 FROM [dbo].[users] 
        WHERE [password_hash] IS NULL OR LEN([password_hash]) < 10
    )
    BEGIN
        RAISERROR(N'LỖI TOÀN VẸN: Phát hiện tài khoản bị thiếu hoặc hỏng password_hash!', 16, 1);
    END;

    -- Báo cáo phân bố tài khoản mới
    PRINT N'Thống kê số lượng tài khoản theo vai trò sau migration:';
    SELECT 
        [role], 
        COUNT(*) AS [UserCount]
    FROM [dbo].[users]
    GROUP BY [role];

    -- Kiểm tra danh sách tài khoản quản trị viên mới
    PRINT N'Danh sách tài khoản ADMIN hiện tại:';
    SELECT [id], [username], [full_name], [role], [created_at]
    FROM [dbo].[users]
    WHERE [role] = 'ADMIN';

    -- Hoàn tất transaction an toàn
    COMMIT TRANSACTION;
    PRINT N'=== MIGRATION HOÀN TẤT THÀNH CÔNG VÀ ĐÃ COMMIT ===';

END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
    BEGIN
        ROLLBACK TRANSACTION;
        PRINT N'ĐÃ ROLLBACK TRANSACTION DO XẢY RA LỖI!';
    END;

    DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
    DECLARE @ErrorState INT = ERROR_STATE();
    DECLARE @ErrorLine INT = ERROR_LINE();

    RAISERROR(N'LỖI THỰC THI MIGRATION (Dòng %d): %s', @ErrorSeverity, @ErrorState, @ErrorLine, @ErrorMessage);
END CATCH;
GO
