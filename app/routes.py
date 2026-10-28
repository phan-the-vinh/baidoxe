import json
import re
import time
from collections import OrderedDict
from threading import Lock
from datetime import timedelta
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, session, flash, send_file
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import select, or_, func
from .extensions import db
from .models import User, Area, Slot, Visit, Rate, MonthlyPass, PassPeriod, Alert, BackupRun, utcnow
from .services.common import (BusinessError, admin_only, manager_only, staff_or_admin_required,
    normalize_plate, field, confirmed, integer, today, write_lock, audit)
from .services import parking
from .services.analytics import snapshot, statistics, date_range

web = Blueprint('web', __name__)
api = Blueprint('api', __name__, url_prefix='/api')
_attempts, _attempts_lock = OrderedDict(), Lock()
_register_attempts, _register_attempts_lock = OrderedDict(), Lock()

def data():
    result = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(result, dict):
        raise BusinessError('Dữ liệu gửi lên không hợp lệ.')
    return result

def finish(message='Đã lưu thay đổi.', redirect_to=None, **extra):
    db.session.commit()
    return jsonify(message=message, redirect=redirect_to, **extra)

def get_visit(visit_id):
    return db.get_or_404(Visit, visit_id)

def parking_context():
    slots = list(db.session.scalars(select(Slot).order_by(Slot.code)))
    active = {v.slot_code:v for v in db.session.scalars(select(Visit).where(Visit.exited_at.is_(None)))}
    return {'slots': slots, 'areas': list(db.session.scalars(select(Area).order_by(Area.code))), 'active': active, 'summary': snapshot()}

@web.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'GET' and current_user.is_authenticated:
        return redirect(url_for('web.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()[:64]
        key = (request.remote_addr, username)
        now = time.monotonic()
        with _attempts_lock:
            failures = [t for t in _attempts.get(key, []) if now-t < 300]
            if len(failures) >= 8:
                return render_template('login.html', error='Bạn đã thử nhiều lần. Hãy chờ 5 phút.'), 429
        user = db.session.scalar(select(User).where(User.username == username))
        if user and user.check_password(request.form.get('password', '')):
            session.clear()
            login_user(user)
            session.permanent = True
            with _attempts_lock:
                _attempts.pop(key, None)
            return redirect(url_for('web.dashboard'))
        with _attempts_lock:
            _attempts[key] = failures + [now]
            while len(_attempts) > 1000:
                _attempts.popitem(last=False)
        return render_template('login.html', error='Tên đăng nhập hoặc mật khẩu chưa đúng.'), 401
    return render_template('login.html')

@web.get('/register')
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for('web.dashboard'))
    return render_template('register.html')

@web.post('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('web.login'))

@web.get('/')
@login_required
def dashboard():
    if current_user.role == 'CUSTOMER':
        from .services.parking import current_rate
        summary = snapshot()
        rate = current_rate()
        return render_template('customer_dashboard.html', summary=summary, rate=rate)
    recent = list(db.session.scalars(select(Visit).order_by(Visit.entered_at.desc()).limit(6)))
    stats = statistics() if current_user.role in ('ADMIN', 'MANAGER') else None
    return render_template('dashboard.html', recent=recent, stats=stats, **parking_context())

@web.get('/slots')
@staff_or_admin_required
def slots():
    alerts = list(db.session.scalars(select(Alert).where(Alert.resolved_at.is_(None)).order_by(Alert.created_at.desc())))
    return render_template('slots.html', alerts=alerts, **parking_context())

@web.get('/entry')
@staff_or_admin_required
def entry():
    available = list(db.session.scalars(select(Slot).where(Slot.state=='EMPTY', Slot.needs_review == False).order_by(Slot.code)))
    return render_template('entry.html', available=available)

@web.get('/exit')
@staff_or_admin_required
def exit_page():
    query = request.args.get('q','').strip()[:64]
    statement = select(Visit).where(Visit.exited_at.is_(None))
    if query:
        clean = re.sub(r'[\s.\-]', '', query).upper()
        statement = statement.where(or_(Visit.ticket_code == query.upper(), Visit.plate == clean))
    active = list(db.session.scalars(statement.order_by(Visit.entered_at)))
    return render_template('exit.html', visits=active, q=query)

@web.get('/visits')
@staff_or_admin_required
def visits():
    statement = select(Visit)
    q = request.args.get('q', '').strip()[:64]
    status = request.args.get('status', '')
    if q:
        plate = re.sub(r'[\s.\-]', '', q).upper()
        statement = statement.where(or_(Visit.plate.contains(plate, autoescape=True), Visit.ticket_code.contains(q.upper(), autoescape=True)))
    if status == 'active':
        statement = statement.where(Visit.exited_at.is_(None))
    elif status == 'closed':
        statement = statement.where(Visit.exited_at.is_not(None))
    if request.args.get('start') or request.args.get('end'):
        _, _, a, b = date_range(request.args.get('start'), request.args.get('end'))
        statement = statement.where(Visit.entered_at >= a, Visit.entered_at < b)
    page = integer({'page':request.args.get('page', '1')}, 'page', 1, 100000)
    count = db.session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(db.session.scalars(statement.order_by(Visit.entered_at.desc(), Visit.id.desc()).offset((page-1)*25).limit(25)))
    return render_template('visits.html', visits=rows, page=page, total=count, q=q, status=status)

@web.get('/visits/<int:visit_id>')
@staff_or_admin_required
def visit_detail(visit_id):
    visit = get_visit(visit_id)
    all_slots = list(db.session.scalars(select(Slot).order_by(Slot.code)))
    fees = json.loads(visit.fee_breakdown) if visit.fee_breakdown else None
    return render_template('visit.html', visit=visit, all_slots=all_slots, fees=fees)


@web.get('/rates')
@admin_only
def rates():
    return render_template('rates.html', rate=parking.current_rate(), history=list(db.session.scalars(select(Rate).order_by(Rate.id.desc()).limit(20))))

@web.get('/areas')
@admin_only
def areas():
    return render_template(
        'areas.html',
        areas=list(db.session.scalars(select(Area).order_by(Area.code))),
        slots=list(db.session.scalars(select(Slot).order_by(Slot.code))),
    )

@web.get('/users')
@admin_only
def users():
    return render_template('users.html', users=list(db.session.scalars(select(User).where(User.role == 'STAFF').order_by(User.id))))

@web.get('/statistics')
@admin_only
def stats_page():
    stats = statistics(request.args.get('start'), request.args.get('end'))
    return render_template('statistics.html', stats=stats)

@web.get('/chatbot')
@login_required
def chatbot_page():
    return render_template('chatbot.html')

@web.get('/vision')
@staff_or_admin_required
def vision_page():
    from .services.vision import status
    from .services.vision_video import public_job
    requested_job_id = request.args.get('job', '').strip()
    if requested_job_id:
        requested_job = public_job(requested_job_id, current_user.id)
        if requested_job is None:
            raise BusinessError('Job phân tích video không tồn tại hoặc không thuộc tài khoản hiện tại.', 404)
        session['parking_analysis_job_id'] = requested_job_id
    job = public_job(session.get('parking_analysis_job_id'), current_user.id)
    return render_template(
        'vision.html',
        model_status=status(),
        video_job=job,
        slot_ids=[f'D{number:02d}' for number in range(1, 25)],
        has_compatible_polygons=bool(job and job.get('has_compatible_polygons')),
    )

@web.get('/vision/video')
@staff_or_admin_required
def vision_video():
    from .services.vision_video import source_video
    path, mimetype = source_video(
        session.get('parking_analysis_job_id'), current_user.id
    )
    return send_file(
        path,
        mimetype=mimetype,
        conditional=True,
        download_name=path.name,
        max_age=0,
    )

@web.get('/ai-demo')
@staff_or_admin_required
def ai_demo_page():
    from .services.demo_ai_playback import (
        DEMO_VIDEO_PATH, DemoPlaybackDataError, load_demo_bundle,
    )
    if not DEMO_VIDEO_PATH.is_file():
        return render_template(
            'error.html', message='Chưa có video demo đã xử lý trước.', status=503
        ), 503
    try:
        payload = load_demo_bundle()
    except DemoPlaybackDataError:
        return render_template(
            'error.html', message='Dữ liệu trạng thái video demo không hợp lệ.', status=503
        ), 503
    return render_template(
        'ai_demo.html',
        slot_ids=payload['slot_ids'],
        demo_artifact_version=payload['artifact_version'],
    )

@web.get('/ai-demo/video')
@staff_or_admin_required
def ai_demo_video():
    from .services.demo_ai_playback import (
        DEMO_VIDEO_PATH, DemoPlaybackDataError, load_demo_bundle,
    )
    if not DEMO_VIDEO_PATH.is_file():
        return render_template(
            'error.html', message='Chưa có video demo đã xử lý trước.', status=404
        ), 404
    try:
        load_demo_bundle()
    except DemoPlaybackDataError:
        return render_template(
            'error.html', message='Bộ video và trạng thái demo không đồng nhất.', status=503
        ), 503
    # A fixed server-side path plus conditional responses enables safe browser seeking.
    return send_file(
        DEMO_VIDEO_PATH,
        mimetype='video/mp4',
        conditional=True,
        download_name='yolo_polygon_demo.mp4',
        max_age=0,
    )

@web.get('/backups')
@admin_only
def backups():
    return render_template('backups.html', backups=list(db.session.scalars(select(BackupRun).order_by(BackupRun.id.desc()).limit(30))))

@api.post('/register')
def api_register():
    if current_user.is_authenticated:
        raise BusinessError('Bạn đã đăng nhập.', 400)
    ip = request.remote_addr or 'unknown'
    now = time.monotonic()
    with _register_attempts_lock:
        failures = [t for t in _register_attempts.get(ip, []) if now - t < 300]
        if len(failures) >= 5:
            raise BusinessError('Bạn đã gửi yêu cầu quá nhiều lần. Vui lòng chờ 5 phút.', 429)
    payload = data()
    username = str(payload.get('username') or '').strip().lower()[:64]
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}', username):
        raise BusinessError('Tên đăng nhập từ 3–64 ký tự: chữ thường, số, _, . hoặc -.', 400)
    full_name = str(payload.get('full_name') or '').strip()[:120]
    if not full_name:
        raise BusinessError('Họ và tên không được để trống.', 400)
    password = str(payload.get('password') or '')
    confirm_password = str(payload.get('confirm_password') or '')
    if not 10 <= len(password) <= 128:
        raise BusinessError('Mật khẩu cần từ 10 đến 128 ký tự.', 400)
    if password != confirm_password:
        raise BusinessError('Mật khẩu xác nhận không khớp.', 400)
    existing = db.session.scalar(select(User.id).where(User.username == username))
    if existing:
        with _register_attempts_lock:
            _register_attempts[ip] = failures + [now]
            while len(_register_attempts) > 1000:
                _register_attempts.popitem(last=False)
        raise BusinessError('Tên đăng nhập đã tồn tại trong hệ thống.', 409)
    # Gán cố định vai trò CUSTOMER - Tuyệt đối không chấp nhận vai trò khác từ client
    user = User(username=username, full_name=full_name, role='CUSTOMER')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    with _register_attempts_lock:
        _register_attempts[ip] = failures + [now]
        while len(_register_attempts) > 1000:
            _register_attempts.popitem(last=False)
    # Không tự động đăng nhập - Yêu cầu người dùng đăng nhập thủ công
    return finish('Đăng ký tài khoản thành công. Vui lòng đăng nhập.', redirect_to=url_for('web.login'), user_id=user.id)

@api.get('/slots')
@staff_or_admin_required
def api_slots():
    active={v.slot_code:v for v in db.session.scalars(select(Visit).where(Visit.exited_at.is_(None)))}
    return jsonify(summary=snapshot(), slots=[{'code':s.code, 'area_code':s.area_code, 'state':s.state, 'needs_review':s.needs_review,
        'ai_state':s.ai_state, 'confidence':s.ai_confidence,
        'visit_id':active[s.code].id if s.code in active else None,
        'plate':active[s.code].plate if s.code in active else None}
        for s in db.session.scalars(select(Slot).order_by(Slot.code))])

@api.get('/plate-check')
@staff_or_admin_required
def api_plate_check():
    plate = normalize_plate(request.args.get('plate'))
    active = db.session.scalar(select(Visit.id).where(Visit.plate == plate, Visit.exited_at.is_(None)))
    return jsonify(plate=plate, active_visit=active, ticket_type='TURN',
        message='Biển số hợp lệ; chưa có lượt gửi trong bãi.' if not active else 'Xe đang có lượt Đang gửi. Không thể nhận thêm.')

@api.post('/visits')
@staff_or_admin_required
def api_entry():
    visit = parking.check_in(data(), current_user.id)
    return finish('Đã ghi nhận xe vào.', url_for('web.visit_detail', visit_id=visit.id), visit_id=visit.id)

@api.get('/visits/lookup')
@staff_or_admin_required
def api_visit_lookup():
    raw_plate = request.args.get('plate', '').strip()
    if not raw_plate:
        raise BusinessError('Vui lòng cung cấp biển số để tra cứu.')
    clean = re.sub(r'[\s.\-]', '', raw_plate).upper()
    visit = db.session.scalar(
        select(Visit).where(
            Visit.plate == clean,
            Visit.exited_at.is_(None)
        ).order_by(Visit.entered_at.desc())
    )
    if not visit:
        return jsonify(
            found=False,
            plate=raw_plate,
            message=f'Không tìm thấy lượt gửi đang hoạt động cho biển số {raw_plate}.'
        )
    return jsonify(
        found=True,
        plate=visit.plate,
        visit={
            'id': visit.id,
            'ticket_code': visit.ticket_code,
            'plate': visit.plate,
            'slot_code': visit.slot_code,
            'entered_at': visit.entered_at.isoformat(),
            'detail_url': url_for('web.visit_detail', visit_id=visit.id),
            'exit_url': url_for('web.exit_page', q=visit.ticket_code),
        },
        message=f'Tìm thấy xe {visit.plate} đang đỗ tại ô {visit.slot_code} (Mã vé: {visit.ticket_code}).'
    )

@api.get('/visits/<int:visit_id>/quote')
@staff_or_admin_required
def api_quote(visit_id):
    return jsonify(fees=parking.quote(get_visit(visit_id)))

@api.post('/visits/<int:visit_id>/checkout')
@staff_or_admin_required
def api_checkout(visit_id):
    visit = parking.check_out(visit_id, data(), current_user.id)
    return finish('Đã hoàn tất xe ra.', url_for('web.visit_detail', visit_id=visit.id))

@api.post('/visits/<int:visit_id>/move')
@staff_or_admin_required
def api_move(visit_id):
    parking.move_visit(visit_id, data(), current_user.id)
    return finish(redirect_to=url_for('web.slots'))

@api.post('/slots/<code>/confirm')
@staff_or_admin_required
def api_confirm_slot(code):
    parking.set_slot(code, data(), current_user.id)
    return finish(redirect_to=url_for('web.slots'))

@api.post('/rates')
@admin_only
def api_rates():
    payload = data()
    turn, night = integer(payload, 'turn_fee'), integer(payload, 'night_fee')
    month = integer(payload, 'monthly_fee') if payload.get('monthly_fee') not in (None, '') else None
    write_lock()
    db.session.add(Rate(turn_fee=turn, night_fee=night, monthly_fee=month, created_by=current_user.id))
    audit(current_user.id, 'UPDATE_RATE', {'turn_fee':turn,'night_fee':night,'monthly_fee':month})
    return finish('Đã lưu bảng giá mới; các lượt đang gửi giữ giá cũ.', url_for('web.rates'))

@api.post('/areas/<code>')
@admin_only
def api_area(code):
    payload = data()
    name = field(payload, 'name', 60)
    description = str(payload.get('description') or '').strip()
    if len(description)>500:
        raise BusinessError('Mô tả tối đa 500 ký tự.')
    write_lock()
    area = db.get_or_404(Area, code)
    area.name, area.description = name, description
    audit(current_user.id, 'UPDATE_AREA', {'area':code})
    return finish(redirect_to=url_for('web.areas'))

@api.post('/users')
@api.post('/users/<int:user_id>')
@admin_only
def api_user(user_id=None):
    payload = data()
    username, fullname = field(payload, 'username',64).lower(), field(payload,'full_name')
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}', username):
        raise BusinessError('Tên đăng nhập từ 3–64 ký tự: chữ thường, số, _, . hoặc -.')
    password = payload.get('password','')
    if (not user_id or password) and not 10<=len(password)<=128:
        raise BusinessError('Mật khẩu cần từ 10 đến 128 ký tự.')
    write_lock()
    if user_id:
        user = db.get_or_404(User, user_id)
        if user.role != 'STAFF':
            raise BusinessError('Chức năng này chỉ chỉnh sửa tài khoản Người dùng.', 403)
    else:
        user = User(role='STAFF')
    existing = db.session.scalar(select(User.id).where(User.username == username))
    if existing and (not user_id or existing != user_id):
        raise BusinessError('Tên đăng nhập đã được sử dụng.', 409)
    user.username, user.full_name = username, fullname
    if password:
        user.set_password(password)
    if not user_id:
        db.session.add(user)
    audit(current_user.id, 'UPDATE_STAFF' if user_id else 'CREATE_STAFF', {'username':username})
    return finish(redirect_to=url_for('web.users'))

@api.post('/backups')
@admin_only
def api_backup():
    confirmed(data(), 'confirmed')
    from .services.backup import run_backup
    result = run_backup(current_user.id)
    return jsonify(message='Sao lưu và xác minh thành công.', **result)

@api.post('/chatbot')
@login_required
def api_chatbot():
    from .services.chatbot import respond
    payload = data()
    result = respond(
        payload.get('message'),
        history=payload.get('history'),
        user_role=current_user.role,
        user_id=current_user.id
    )
    return jsonify(result)

@api.post('/vision/parking')
@staff_or_admin_required
def api_vision_parking():
    from .services.vision import parking_image
    result = parking_image(request.files.get('image'))
    return finish(predictions=result['predictions'], overlay=result['overlay'])

@api.post('/vision/plate')
@staff_or_admin_required
def api_vision_plate():
    from .services.vision import plate_frame_window,plate_image
    frames=request.files.getlist('frames')
    if frames:
        try:current_index=int(request.form.get('current_index','0'))
        except ValueError:raise BusinessError('Chỉ số frame hiện tại không hợp lệ.') from None
        return jsonify(**plate_frame_window(frames,current_index))
    return jsonify(**plate_image(request.files.get('image')))

@api.get('/vision/status')
@staff_or_admin_required
def api_vision_status():
    from .services.vision import status
    return jsonify(**status())

@api.post('/vision/video')
@staff_or_admin_required
def api_vision_video_create():
    from .services.vision_video import create_job
    job = create_job(request.files.get('video'), current_user.id)
    session['parking_analysis_job_id'] = job['id']
    return jsonify(job=job), 202

@api.get('/vision/video/status')
@staff_or_admin_required
def api_vision_video_status():
    from .services.vision_video import public_job
    job = public_job(session.get('parking_analysis_job_id'), current_user.id)
    if job is None:
        return jsonify(error='Chưa có video nhận diện trong phiên làm việc này.'), 404
    return jsonify(job=job)

@api.get('/vision/video/states')
@staff_or_admin_required
def api_vision_video_states():
    from .services.vision_video import timeline
    response = jsonify(timeline(session.get('parking_analysis_job_id'), current_user.id))
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    return response

@api.get('/vision/video/detections')
@staff_or_admin_required
def api_vision_video_detections():
    from .services.vision_video import get_job_detections
    response = jsonify(get_job_detections(session.get('parking_analysis_job_id'), current_user.id))
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    return response

@api.get('/ai-demo/states')
@staff_or_admin_required
def api_ai_demo_states():
    from .services.demo_ai_playback import DemoPlaybackDataError, load_demo_bundle
    try:
        return jsonify(load_demo_bundle())
    except DemoPlaybackDataError:
        return jsonify(error='Dữ liệu trạng thái video demo không hợp lệ.'), 503
