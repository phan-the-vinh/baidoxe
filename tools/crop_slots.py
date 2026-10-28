import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from ai.roi import load_rois,crop_roi

def main():
    parser=argparse.ArgumentParser(description='Cắt 20 ảnh ô; cần gán nhãn thủ công sau khi cắt')
    parser.add_argument('--image',required=True)
    parser.add_argument('--roi',default='config/parking_slots.json')
    parser.add_argument('--output',default='dataset/unlabeled')
    parser.add_argument('--group',required=True,help='Mã camera_ngay_phien để tránh chia cùng phiên qua các tập')
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.group):raise SystemExit('group chỉ dùng chữ, số, _ và -.')
    frame=cv2.imdecode(np.fromfile(args.image,np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise SystemExit('Không đọc được ảnh.')
    output=Path(args.output)/args.group;output.mkdir(parents=True,exist_ok=True)
    for code,points in load_rois(args.roi,frame.shape).items():
        destination=output/f'{Path(args.image).stem}_{code}.png'
        if destination.exists():raise SystemExit(f'Không ghi đè ảnh đã có: {destination}')
        cv2.imencode('.png',crop_roi(frame,points))[1].tofile(destination)
    print('Đã cắt 20 ô. Gán nhãn empty/occupied bằng quan sát thực tế, không dùng dự đoán AI làm ground truth.')

if __name__=='__main__':main()
