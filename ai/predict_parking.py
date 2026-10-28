import argparse
import json
import threading
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from ai.roi import load_rois,crop_roi,rgb_input,draw_predictions
from ai.data import sha256_file

class ParkingPredictor:
    def __init__(self,model_path):
        self.path=Path(model_path)
        if not self.path.exists():raise ValueError('Chưa có mô hình đã huấn luyện. Hệ thống không tạo dự đoán giả.')
        metadata_path=self.path.with_suffix('.metadata.json')
        if not metadata_path.exists():raise ValueError('Thiếu metadata của mô hình. Dùng script train của dự án.')
        metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('pretrained') is not False or metadata.get('class_names')!=['empty','occupied'] or metadata.get('normalization')!='inside_model_0_255_to_0_1':
            raise ValueError('Metadata không khớp CNN tự xây dựng của dự án.')
        if metadata.get('model_sha256')!=sha256_file(self.path):raise ValueError('Model không khớp metadata; cần dùng cùng bộ kết quả huấn luyện.')
        import tensorflow as tf
        self.model=tf.keras.models.load_model(self.path,compile=False)
        self.size=int(metadata['input_size'])
        if self.model.input_shape[1:]!=(self.size,self.size,3) or self.model.output_shape[-1]!=1:
            raise ValueError('Kích thước đầu vào/đầu ra của model không hợp lệ.')
        self.lock=threading.Lock()

    def predict(self,frame,roi_path):
        rois=load_rois(roi_path,frame.shape)
        inputs=np.stack([rgb_input(crop_roi(frame,p),self.size) for p in rois.values()])
        # model chua Rescaling. Khong chia 255 lan thu hai.
        with self.lock:probabilities=self.model(inputs,training=False).numpy().ravel()
        if len(probabilities)!=20 or not np.isfinite(probabilities).all() or np.any(probabilities<0) or np.any(probabilities>1):raise ValueError('Model trả kết quả không hợp lệ.')
        result=[{'slot':code,'state':'OCCUPIED' if p>=0.5 else 'EMPTY',
                 'confidence':float(max(p,1-p)),'occupied_probability':float(p)} for code,p in zip(rois,probabilities)]
        return result,rois

def main():
    parser=argparse.ArgumentParser(description='Nhận diện ảnh độc lập, không ghi dữ liệu nghiệp vụ')
    parser.add_argument('--image',required=True)
    parser.add_argument('--model',default='ai_models/parking_classifier.keras')
    parser.add_argument('--roi',default='config/parking_slots.json')
    parser.add_argument('--output',default='ai/results/prediction.jpg')
    args=parser.parse_args()
    frame=cv2.imdecode(np.fromfile(args.image,dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise ValueError('Không đọc được ảnh.')
    predictions,rois=ParkingPredictor(args.model).predict(frame,args.roi)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    cv2.imencode('.jpg',draw_predictions(frame,rois,predictions))[1].tofile(target)
    print(json.dumps(predictions,indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,FileNotFoundError,ImportError) as exc:raise SystemExit(str(exc))
