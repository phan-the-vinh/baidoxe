from datetime import timedelta
import io
import json
import pytest
from sqlalchemy import select,func
from app.extensions import db
from app.models import (Visit,Slot,Payment,Rate,Alert,Observation,MonthlyPass,
                        PassPeriod,ParkingCapacityHistory,TARGET_SLOT_CODES,utcnow)
from app.services.common import today
from app.services.parking import accept_observations

def enter(client,plate='30A12345',slot='D01'):
    return client.post('/api/visits',json={'plate':plate,'slot_code':slot,'plate_confirmed':True,'slot_confirmed':True})

def checkout(client,visit_id,**extra):
    result=client.get(f'/api/visits/{visit_id}/quote')
    return client.post(f'/api/visits/{visit_id}/checkout',json={'quoted_amount':result.json.get('fees',{}).get('total',0),'vehicle_confirmed':True,'paid_confirmed':True,**extra})

@pytest.mark.parametrize('url',['/','/slots','/entry','/exit','/visits','/rates','/areas','/users','/statistics','/chatbot','/vision','/backups'])
def test_manager_pages(manager,url):
    response=manager.get(url)
    assert response.status_code==200,response.data.decode()[:400]

@pytest.mark.parametrize('url',['/rates','/areas','/users','/statistics','/backups'])
def test_staff_cannot_access_manager_pages(staff,url):assert staff.get(url).status_code==403

@pytest.mark.parametrize('url',['/api/rates','/api/areas/D','/api/users','/api/backups'])
def test_staff_cannot_access_manager_apis(staff,url):assert staff.post(url,json={}).status_code==403

def test_entry_atomicity_and_duplicates(manager,app):
    first=enter(manager);assert first.status_code==200
    assert enter(manager).status_code==409
    assert enter(manager,'30A99999').status_code==409
    assert enter(manager,'30A11111','C01').status_code==409
    assert manager.post('/api/visits',json={'plate':'30A22222','slot_code':'D02'}).status_code==400
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Visit))==1
        assert db.session.get(Slot,'D01').state=='OCCUPIED'
        assert db.session.get(Slot,'D02').state=='EMPTY'

def test_checkout_exactly_once_and_payment_required(manager,app):
    visit_id=enter(manager).json['visit_id']
    assert checkout(manager,visit_id,paid_confirmed=False).status_code==400
    assert checkout(manager,visit_id,vehicle_confirmed=False).status_code==400
    assert checkout(manager,visit_id,quoted_amount=0).status_code==409
    assert checkout(manager,visit_id).status_code==200
    assert checkout(manager,visit_id).status_code==409
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Payment))==1
        assert db.session.get(Slot,'D01').state=='EMPTY'

def test_rate_snapshot(manager,app):
    id=enter(manager).json['visit_id']
    assert manager.post('/api/rates',json={'turn_fee':33000,'night_fee':99000,'monthly_fee':500000}).status_code==200
    assert manager.get(f'/api/visits/{id}/quote').json['fees']['total']==20000
    other=enter(manager,'29A99999','D02').json['visit_id']
    assert manager.get(f'/api/visits/{other}/quote').json['fees']['total']==33000

def test_lost_ticket_flow_removed_and_regular_checkout(manager,app):
    id=enter(manager).json['visit_id']
    assert checkout(manager,id).status_code==200
    with app.app_context():
        visit=db.session.get(Visit,id)
        assert visit.amount==20000 and not visit.lost_ticket

def test_manual_guard_move_and_no_new_slots(manager,app):
    id=enter(manager).json['visit_id']
    payload={'state':'EMPTY','confirmed':True,'reason':'Đã kiểm tra thực tế'}
    assert manager.post('/api/slots/D01/confirm',json=payload).status_code==409
    assert manager.post(f'/api/visits/{id}/move',json={'slot_code':'D11','confirmed':True,'reason':'Xe đỗ sai ô'}).status_code==200
    with app.app_context():
        assert db.session.get(Slot,'D01').state=='EMPTY'
        assert db.session.get(Slot,'D11').state=='OCCUPIED'
        assert db.session.get(Visit,id).slot_code=='D11'
    assert manager.post('/api/areas',json={'code':'C'}).status_code==404
    assert manager.delete('/api/slots/D01').status_code in (404,405)

def test_full_parking(manager,app):
    with app.app_context():
        for slot in db.session.scalars(select(Slot)):slot.state='OCCUPIED'
        db.session.commit()
    assert enter(manager).status_code==409

def test_monthly_feature_removed_and_entry_always_turn(manager,app):
    assert manager.get('/monthly').status_code==404
    assert manager.get('/monthly/new').status_code==404
    assert manager.post('/api/monthly',json={}).status_code==404
    resp = manager.get('/')
    assert resp.status_code==200
    html = resp.data.decode()
    assert 'Vé tháng' not in html
    assert 'Xe ra · Mất vé' not in html
    assert 'Xe ra' in html
    visit_id=enter(manager,'30A12345','D01').json['visit_id']
    with app.app_context():
        visit=db.session.get(Visit,visit_id)
        assert visit.ticket_type=='TURN'

def test_ai_conflict_low_confidence_stale(manager,app):
    enter(manager)
    with app.app_context():
        accept_observations([{'slot':'D01','state':'EMPTY','confidence':.97}],utcnow(),.8);db.session.commit()
        slot=db.session.get(Slot,'D01');assert slot.state=='OCCUPIED' and slot.needs_review
        accept_observations([{'slot':'D02','state':'EMPTY','confidence':.51}],utcnow(),.8);db.session.commit()
        assert db.session.get(Slot,'D02').needs_review
        slot=db.session.get(Slot,'D03');before=slot.changed_at-timedelta(seconds=1)
        accept_observations([{'slot':'D03','state':'OCCUPIED','confidence':.99}],before,.8);db.session.commit()
        assert slot.state=='EMPTY' and not slot.needs_review and slot.ai_state is None
        assert db.session.scalar(select(func.count()).select_from(Alert))==2
    assert enter(manager,'30B12345','D02').status_code==409
    assert manager.post('/api/slots/D02/confirm',json={'state':'EMPTY','confirmed':True,'reason':'Đã kiểm tra, ô trống'}).status_code==200
    assert enter(manager,'30B12345','D02').status_code==200

def test_validation_search_and_staff_management(manager):
    assert enter(manager,'<script>').status_code==400
    assert manager.get('/visits?start=2026-09-10&end=2026-09-01').status_code==400
    assert manager.get('/statistics?start=abc').status_code==400
    assert manager.get('/visits?page=not_number').status_code==400
    assert manager.post('/api/rates',json={'turn_fee':-1,'night_fee':80000,'monthly_fee':1}).status_code==400
    assert manager.post('/api/users',json={'username':'newstaff','full_name':'Nhân viên mới','password':'strong-test-only'}).status_code==200
    assert manager.post('/api/users',json={'username':'newstaff','full_name':'Nhân viên trùng','password':'strong-test-only'}).status_code==409
    assert manager.post('/api/users/1',json={'username':'manager','full_name':'Manager','password':''}).status_code==403

def test_removed_gemini_assistant_routes_and_backup_no_sqlserver(manager):
    assert manager.get('/gemini').status_code==404
    assert manager.post('/api/gemini',json={'mode':'report'}).status_code==404
    assert manager.post('/api/backups',json={'confirmed':True}).status_code==503

def test_sidebar_keeps_chatbot_and_removes_gemini_assistant(manager):
    page = manager.get('/')
    assert page.status_code == 200
    assert b'href="/chatbot"' in page.data
    assert b'href="/gemini"' not in page.data
    assert 'Trợ lý Gemini'.encode('utf-8') not in page.data

def test_vision_missing_model_and_bad_upload(manager,app):
    import cv2,numpy as np
    image=cv2.imencode('.jpg',np.zeros((120,160,3),np.uint8))[1].tobytes()
    response=manager.post('/api/vision/parking',data={'image':(io.BytesIO(image),'x.jpg')})
    assert response.status_code==409
    assert '20 ROI' in response.json['error']
    assert manager.post('/api/vision/plate',data={'image':(io.BytesIO(b'invalid'),'bad.jpg')}).status_code==400
    with app.app_context():assert db.session.scalar(select(func.count()).select_from(Observation))==0

def test_csrf_required(app):
    app.config['WTF_CSRF_ENABLED']=True
    with app.test_client() as client:
        assert client.post('/login',data={'username':'manager','password':'test-manager-only'}).status_code==400

def test_real_csrf_login_and_api(app):
    import re
    app.config['WTF_CSRF_ENABLED']=True
    with app.test_client() as client:
        page=client.get('/login')
        token=re.search(r'name="csrf_token" value="([^"]+)"',page.data.decode()).group(1)
        response=client.post('/login',data={'username':'manager','password':'test-manager-only','csrf_token':token})
        assert response.status_code==302
        page=client.get('/entry')
        token=re.search(r'name="csrf-token" content="([^"]+)"',page.data.decode()).group(1)
        response=client.post('/api/visits',json={'plate':'30A12345','slot_code':'D01','plate_confirmed':True,'slot_confirmed':True},headers={'X-CSRFToken':token})
        assert response.status_code==200
        assert client.post('/api/visits',json={}).status_code==400

def test_stats_revenue_only_confirmed(manager,app):
    from app.services.analytics import statistics
    id=enter(manager).json['visit_id']
    with app.app_context():assert statistics()['revenue']==0
    checkout(manager,id)
    with app.app_context():
        stats=statistics();assert stats['revenue']==20000 and stats['arrivals']==1 and stats['departures']==1

def test_seeded_layout_is_d01_to_d24(app):
    with app.app_context():
        assert tuple(db.session.scalars(select(Slot.code).order_by(Slot.code)))==TARGET_SLOT_CODES
        history=list(db.session.scalars(select(ParkingCapacityHistory)))
        assert len(history)==1 and history[0].capacity==24

def test_capacity_history_uses_cutover_timestamp(app):
    from datetime import datetime
    from app.services.analytics import capacity_at,capacity_seconds
    cutover=datetime(2026,9,24,4,25,58,286680)
    events=[(datetime(1900,1,1),20),(cutover,24)]
    assert capacity_at(cutover-timedelta(microseconds=1),events)==20
    assert capacity_at(cutover,events)==24
    assert capacity_seconds(cutover-timedelta(seconds=10),cutover+timedelta(seconds=10),events)==440
