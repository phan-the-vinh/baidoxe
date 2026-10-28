import base64
import io
import json
import os
import re
import threading
from pathlib import Path
from PIL import Image, UnidentifiedImageError
import cv2
import numpy as np
from ..config import ROOT,project_path
from ..models import utcnow
from .common import BusinessError
from .parking import accept_observations
from .parking_layout import current_layout
from ai.roi import load_rois,draw_predictions
from ai.data import sha256_file

_predictors={}
_model_lock=threading.Lock()
_inference_gate=threading.BoundedSemaphore(1)

def read_image(upload):
    if not upload:raise BusinessError('Hãy chọn ảnh hoặc chụp khung hình camera.')
    raw=upload.read(15*1024*1024+1)
    if len(raw)>15*1024*1024:raise BusinessError('Ảnh tối đa 15 MB.',413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('JPEG','PNG','WEBP') or image.width*image.height>16_000_000:
                raise BusinessError('Ảnh phải là JPEG/PNG/WebP, tối đa 16 triệu pixel.')
            image.verify()
        frame=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if frame is None:raise ValueError('decode')
        return frame
    except (UnidentifiedImageError,ValueError,OSError,Image.DecompressionBombError):
        raise BusinessError('Tệp ảnh bị lỗi hoặc định dạng không được hỗ trợ.') from None

def legacy_status():
    layout=current_layout()
    if layout['name']=='D24':
        return {
            'ready':False,
            'message':'CNN ảnh 20 ROI A/B đã được vô hiệu hóa trên cấu hình D01–D24; không ánh xạ tự động và không ghi trạng thái vào database.',
            'metrics':None,
        }
    if not layout['legacy_cnn_write_enabled']:
        return {
            'ready':False,
            'message':'Cấu hình vị trí hiện tại không tương thích CNN ảnh 20 ROI A/B; dữ liệu được giữ nguyên.',
            'metrics':None,
        }
    model=project_path('PARKING_MODEL_PATH','ai_models/parking_classifier.keras')
    roi=project_path('PARKING_ROI_PATH','config/parking_slots.json')
    metrics=None
    try:
        if not model.exists():raise ValueError('Chưa có mô hình đã huấn luyện. Xem hướng dẫn dataset và train trong README.')
        metadata=json.loads(model.with_suffix('.metadata.json').read_text(encoding='utf-8'))
        digest=sha256_file(model)
        if metadata.get('model_sha256')!=digest or metadata.get('pretrained') is not False:
            raise ValueError('Model chưa khớp metadata huấn luyện của dự án.')
        path=ROOT/'ai/results/metrics.json'
        if path.exists():
            candidate=json.loads(path.read_text(encoding='utf-8'))
            if candidate.get('model_sha256')==digest and candidate.get('samples',0)>0 and all(k in candidate for k in ['accuracy','precision','recall','f1','loss','evaluated_at']):
                metrics=candidate
        load_rois(roi)
        return {'ready':True,'message':'Đã có tệp mô hình và cấu hình 20 ROI. Mô hình được nạp ở lần nhận diện đầu tiên.','metrics':metrics}
    except (ValueError,FileNotFoundError,KeyError):
        import sys
        return {'ready':False,'message':str(sys.exc_info()[1]),'metrics':metrics}

def status():
    from .vision_video import status as video_status
    result=video_status()
    legacy=legacy_status()
    result['metrics']=None
    result['legacy_classifier']=legacy
    return result

def parking_image(upload):
    layout=current_layout()
    if not layout['legacy_cnn_write_enabled']:
        raise BusinessError(
            'CNN ảnh 20 ROI A/B không được phép ghi trạng thái trên cấu hình D01–D24. Hãy dùng video YOLO + Polygon hoặc kiểm tra thủ công.',
            409,
        )
    captured=utcnow()
    frame=read_image(upload)
    if not _inference_gate.acquire(blocking=False):raise BusinessError('AI đang xử lý khung hình khác. Hãy thử lại sau.',429)
    try:
        from ai.predict_parking import ParkingPredictor
        model=project_path('PARKING_MODEL_PATH','ai_models/parking_classifier.keras')
        if not model.exists():raise BusinessError('Chưa có mô hình đã huấn luyện. Trạng thái bãi xe được giữ nguyên.',503)
        meta=model.with_suffix('.metadata.json')
        key=(str(model),model.stat().st_mtime_ns,meta.stat().st_mtime_ns if meta.exists() else 0)
        with _model_lock:
            if key not in _predictors:
                predictor=ParkingPredictor(model)
                _predictors.clear();_predictors[key]=predictor
            predictor=_predictors[key]
        predictions,rois=predictor.predict(frame,project_path('PARKING_ROI_PATH','config/parking_slots.json'))
        threshold=float(os.getenv('AI_CONFIDENCE_THRESHOLD','0.8'))
        if not 0.5<=threshold<=1:raise ValueError('AI_CONFIDENCE_THRESHOLD cần trong khoảng 0.5–1.')
        accept_observations(predictions,captured,threshold)
        overlay=draw_predictions(frame,rois,predictions)
        if overlay.shape[1]>1600:
            overlay=cv2.resize(overlay,(1600,round(overlay.shape[0]*1600/overlay.shape[1])))
        encoded=cv2.imencode('.jpg',overlay,[cv2.IMWRITE_JPEG_QUALITY,80])[1]
        return {'predictions':predictions,'overlay':base64.b64encode(encoded).decode('ascii')}
    except BusinessError:raise
    except (ValueError,FileNotFoundError,ImportError) as exc:
        raise BusinessError(str(exc),503) from None
    except Exception:
        raise BusinessError('Không nạp/chạy được mô hình. Kiểm tra TensorFlow, model và ROI; dữ liệu được giữ nguyên.',503) from None
    finally:_inference_gate.release()

def plate_image(upload):
    from .plate_detection import detect_vehicles_and_plates
    return detect_vehicles_and_plates(read_image(upload))

def plate_frame_window(uploads, current_index):
    from .plate_detection import detect_plate_frame_window
    return detect_plate_frame_window([read_image(upload) for upload in uploads], current_index)
