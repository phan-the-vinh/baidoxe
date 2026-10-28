"""Click 4 goc theo chieu kim dong ho cho tung o; Enter luu o, S luu du 20 o."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from ai.roi import SLOT_CODES,load_rois

def main():
    parser=argparse.ArgumentParser(description='Đánh dấu 20 polygon trên ảnh thật')
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',default='config/parking_slots.json')
    args=parser.parse_args()
    frame=cv2.imdecode(np.fromfile(args.image,np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise SystemExit('Không đọc được ảnh.')
    height,width=frame.shape[:2];scale=min(1,1400/width,850/height)
    points=[];slots={};index=0;window='ROI editor: 4 corners -> Enter | U undo | R reset | Backspace previous | S save | Esc cancel'
    def click(event,x,y,flags,param):
        if event==cv2.EVENT_LBUTTONDOWN and len(points)<4:
            points.append([min(width-1,round(x/scale)),min(height-1,round(y/scale))])
    cv2.namedWindow(window,cv2.WINDOW_AUTOSIZE);cv2.setMouseCallback(window,click)
    try:
        while True:
            canvas=frame.copy()
            for code,p in slots.items():
                poly=np.asarray(p,np.int32);cv2.polylines(canvas,[poly],True,(60,180,50),2);cv2.putText(canvas,code,tuple(poly[0]),cv2.FONT_HERSHEY_SIMPLEX,.8,(60,180,50),2)
            for p in points:cv2.circle(canvas,tuple(p),5,(0,165,255),-1)
            if len(points)>1:cv2.polylines(canvas,[np.asarray(points,np.int32)],len(points)==4,(0,165,255),2)
            display=cv2.resize(canvas,None,fx=scale,fy=scale)
            label=f'{SLOT_CODES[index] if index<20 else "COMPLETE"} | {len(slots)}/20 slots | {len(points)}/4 corners'
            cv2.rectangle(display,(0,0),(min(display.shape[1],650),45),(25,35,35),-1)
            cv2.putText(display,label,(12,30),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2)
            cv2.imshow(window,display);key=cv2.waitKey(20)&0xFF
            if key==27:print('Đã hủy; không thay đổi cấu hình.');break
            if key==ord('u') and points:points.pop()
            if key==ord('r'):points.clear()
            if key==8 and index>0:
                index-=1;points[:]=slots.pop(SLOT_CODES[index],[])
            if key in (10,13) and len(points)==4 and index<20:
                p=np.asarray(points,np.int32)
                if not cv2.isContourConvex(p) or cv2.contourArea(p)<25:
                    print('Polygon không hợp lệ. Nhấn R rồi đánh dấu lại theo thứ tự quanh ô.');continue
                slots[SLOT_CODES[index]]=points.copy();points.clear();index+=1
            if key==ord('s'):
                if len(slots)!=20:print('Cần đủ 20 ô trước khi lưu.');continue
                target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix('.tmp.json')
                temporary.write_text(json.dumps({'configured':True,'image_width':width,'image_height':height,'slots':slots},indent=2),encoding='utf-8')
                load_rois(temporary,frame.shape)
                if target.exists():target.with_suffix('.previous.json').write_bytes(target.read_bytes())
                temporary.replace(target);print(f'Đã lưu {target}.');break
    finally:cv2.destroyAllWindows()

if __name__=='__main__':main()
