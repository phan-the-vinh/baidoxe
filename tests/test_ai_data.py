import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from ai.roi import SLOT_CODES,load_rois,crop_roi
from ai.data import inspect_dataset

def test_unconfigured_and_invalid_rois(tmp_path):
    target=tmp_path/'roi.json';target.write_text(json.dumps({'configured':False}))
    with pytest.raises(ValueError,match='Chưa cấu hình'):load_rois(target)
    config={'configured':True,'image_width':100,'image_height':100,'slots':{s:[[10,10],[20,10],[20,30],[10,30]] for s in SLOT_CODES}}
    target.write_text(json.dumps(config));assert len(load_rois(target,(100,100,3)))==20
    with pytest.raises(ValueError,match='kích thước'):load_rois(target,(200,200,3))
    config['slots']['A01']=[[0,0],[100,0],[100,30],[0,30]];target.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='ngoài ảnh'):load_rois(target)

def test_cross_split_leakage(tmp_path):
    for split in ['train','validation','test']:
        for i,label in enumerate(['empty','occupied']):
            directory=tmp_path/split/label;directory.mkdir(parents=True)
            cv2.imwrite(str(directory/'duplicate.png'),np.full((32,32,3),i*255,np.uint8))
    with pytest.raises(ValueError,match='Rò rỉ'):inspect_dataset(tmp_path)

def test_empty_dataset_no_fake_metrics(tmp_path):
    with pytest.raises(ValueError,match='Chưa có ảnh'):inspect_dataset(tmp_path)
    assert not (tmp_path/'metrics.json').exists()
