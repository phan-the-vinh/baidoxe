"""Kiem thu canh tranh tren CSDL SQL Server rieng co ten ket thuc _Test.

Khong tu tao/xoa database; khong chay tren CSDL van hanh. Du lieu kiem thu duoc
giu lai trong database Test, moi luot thanh cong duoc dong theo nghiep vu.
"""
import os
import sys
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env')

def main():
    if not os.getenv('DB_NAME','').endswith('_Test'):
        raise SystemExit('Chỉ chạy trên database riêng có tên kết thúc _Test. Không thay đổi dữ liệu vận hành.')
    from app import create_app
    from app.extensions import db
    from app.models import User,Slot
    from app.services.parking import check_in,check_out,quote
    from app.services.common import BusinessError
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    app=create_app()
    with app.app_context():
        if db.engine.dialect.name!='mssql':raise SystemExit('Yêu cầu SQL Server thật.')
        user=db.session.scalar(select(User).where(User.role=='MANAGER'))
        if not user:raise SystemExit('Hãy chạy init-db trên database Test trước.')
        actor=user.id
        slots=[s.code for s in db.session.scalars(select(Slot).where(Slot.state=='EMPTY',Slot.needs_review.is_(False)).order_by(Slot.code).limit(2))]
        if len(slots)<2:raise SystemExit('Database Test cần ít nhất hai ô trống.')

    def scenario(requests):
        barrier=threading.Barrier(2)
        def attempt(payload):
            with app.app_context():
                barrier.wait(timeout=10)
                try:
                    visit=check_in({**payload,'plate_confirmed':True,'slot_confirmed':True},actor)
                    db.session.commit();return {'success':True,'id':visit.id}
                except (BusinessError,IntegrityError):
                    db.session.rollback();return {'success':False}
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,requests))
        succeeded=[r for r in results if r['success']]
        # Dong cac luot tao duoc, ke ca neu phat hien loi canh tranh, bang nghiep vu.
        for result in succeeded:
            with app.app_context():
                from app.models import Visit
                amount=quote(db.session.get(Visit,result['id']))['total']
                check_out(result['id'],{'vehicle_confirmed':True,'paid_confirmed':True,'quoted_amount':amount},actor)
                db.session.commit()
        if len(succeeded)!=1:raise AssertionError('Hai yêu cầu cạnh tranh phải đúng một thành công và một từ chối.')

    plate1='99Z'+str(secrets.randbelow(900000)+100000)
    plate2='99Z'+str(secrets.randbelow(900000)+100000)
    while plate2==plate1:plate2='99Z'+str(secrets.randbelow(900000)+100000)
    scenario([{'plate':plate1,'slot_code':slots[0]},{'plate':plate2,'slot_code':slots[0]}])
    print('PASS: hai xe tranh cùng một ô, chỉ một lượt được tạo.')
    scenario([{'plate':plate1,'slot_code':slots[0]},{'plate':plate1,'slot_code':slots[1]}])
    print('PASS: một biển số đồng thời vào hai ô, chỉ một lượt được tạo.')
    print('Các lượt kiểm thử đã hoàn tất xe ra trong database Test. Chưa kiểm tra backup trong lệnh này.')

if __name__=='__main__':main()
