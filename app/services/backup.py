import os
import re
import ntpath
from datetime import datetime
from sqlalchemy import text
from ..extensions import db
from ..models import BackupRun
from .common import BusinessError, audit

def run_backup(actor):
    if db.engine.dialect.name != 'mssql':
        raise BusinessError('Sao lưu .bak chỉ hoạt động với SQL Server.', 503)
    name = os.getenv('DB_NAME', 'ParkingAI')
    directory = os.getenv('BACKUP_DIR', '')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,100}', name):
        raise BusinessError('DB_NAME chỉ được chứa chữ, số, dấu gạch dưới.')
    if not directory or not (ntpath.isabs(directory) or directory.startswith('/')):
        raise BusinessError('Hãy cấu hình BACKUP_DIR bằng đường dẫn tuyệt đối trên máy SQL Server.')
    filename = f'{name}_{datetime.now():%Y%m%d_%H%M%S_%f}.bak'
    destination = directory.rstrip('\\/') + ('/' if directory.startswith('/') else '\\') + filename
    record = BackupRun(filename=filename, status='RUNNING', created_by=actor)
    db.session.add(record)
    db.session.commit()
    try:
        # BACKUP/RESTORE VERIFYONLY can thuc thi ngoai transaction, khong dung request path.
        with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            cursor=connection.connection.driver_connection.cursor()
            try:
                cursor.execute(f'BACKUP DATABASE [{name}] TO DISK = ? WITH COPY_ONLY, CHECKSUM', destination)
                while cursor.nextset():pass
                cursor.execute('RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM', destination)
                while cursor.nextset():pass
            finally:cursor.close()
        record.status = 'SUCCESS'
        audit(actor, 'BACKUP_SUCCESS', {'file': filename})
        db.session.commit()
    except Exception:
        db.session.rollback()
        record = db.session.get(BackupRun, record.id)
        record.status = 'FAILED'
        db.session.commit()
        raise BusinessError('Sao lưu/xác minh thất bại. Kiểm tra quyền SQL Server, thư mục BACKUP_DIR và dung lượng. Không coi tệp dở dang là bản sao lưu hợp lệ.', 503) from None
    return {'filename': filename, 'directory': directory, 'verified': True}
