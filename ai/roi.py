import json
from pathlib import Path
import cv2
import numpy as np

SLOT_CODES = [f'{a}{i:02d}' for a in 'AB' for i in range(1,11)]

def load_rois(path, frame_shape=None):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    if not config.get('configured'):
        raise ValueError('Chưa cấu hình ROI thật. Chạy tools/parking_roi_editor.py để đánh dấu đủ 20 ô.')
    width,height = config.get('image_width'),config.get('image_height')
    if not isinstance(width,int) or not isinstance(height,int) or width<=0 or height<=0:
        raise ValueError('Kích thước ảnh tham chiếu trong ROI không hợp lệ.')
    if frame_shape is not None and (frame_shape[1]!=width or frame_shape[0]!=height):
        raise ValueError(f'Ảnh cần đúng kích thước ROI {width}×{height}; nhận được {frame_shape[1]}×{frame_shape[0]}. Cấu hình lại ROI cho nguồn này.')
    slots = config.get('slots',{})
    if set(slots)!=set(SLOT_CODES):
        raise ValueError('ROI phải có đúng A01–A10, B01–B10.')
    checked={}
    for code in SLOT_CODES:
        p=np.asarray(slots[code],dtype=np.float32)
        if p.shape!=(4,2) or not np.isfinite(p).all():
            raise ValueError(f'{code}: cần 4 điểm polygon hợp lệ.')
        if np.any(p[:,0]<0) or np.any(p[:,0]>=width) or np.any(p[:,1]<0) or np.any(p[:,1]>=height):
            raise ValueError(f'{code}: tọa độ nằm ngoài ảnh.')
        if not cv2.isContourConvex(p.astype(np.int32)) or cv2.contourArea(p)<25:
            raise ValueError(f'{code}: polygon cần lồi, đủ diện tích và 4 điểm theo thứ tự quanh ô.')
        checked[code]=p.astype(np.int32)
    return checked

def crop_roi(frame,polygon):
    x,y,w,h = cv2.boundingRect(polygon)
    patch = frame[y:y+h,x:x+w]
    mask=np.zeros((h,w),dtype=np.uint8)
    cv2.fillPoly(mask,[polygon-np.array([x,y])],255)
    return cv2.bitwise_and(patch,patch,mask=mask)

def rgb_input(bgr,size=96):
    return cv2.resize(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB),(size,size),interpolation=cv2.INTER_AREA).astype(np.float32)

def draw_predictions(frame,rois,predictions):
    out=frame.copy()
    for item in predictions:
        p=rois[item['slot']]
        color=(82,168,42) if item['state']=='EMPTY' else (65,104,220)
        if item.get('accepted') is False: color=(0,174,240)
        cv2.polylines(out,[p],True,color,2)
        x,y = p.min(axis=0)
        label=f"{item['slot']} {item['state']} {item['confidence']:.2f}"
        cv2.putText(out,label,(int(x),max(16,int(y)-5)),cv2.FONT_HERSHEY_SIMPLEX,.43,color,1,cv2.LINE_AA)
    return out
