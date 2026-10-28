import hashlib
from pathlib import Path
import cv2
import numpy as np
from .roi import rgb_input

CLASSES = ['empty','occupied']
EXTENSIONS = {'.jpg','.jpeg','.png','.bmp','.webp'}

def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as source:
        for block in iter(lambda: source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def scan_split(directory):
    files=[]
    for label,name in enumerate(CLASSES):
        images=sorted(p for p in (Path(directory)/name).rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS)
        if not images:raise ValueError(f'Chưa có ảnh lớp {name}: {Path(directory)/name}. Không thể huấn luyện/đánh giá.')
        for p in images:
            img=cv2.imdecode(np.fromfile(p,dtype=np.uint8),cv2.IMREAD_COLOR)
            if img is None:raise ValueError(f'Không đọc được ảnh {p}.')
        files.extend((str(p),label) for p in images)
    return files

def inspect_dataset(root):
    splits={name:scan_split(Path(root)/name) for name in ['train','validation','test']}
    seen={}; manifest=[]
    for split,files in splits.items():
        for path,label in files:
            digest=sha256_file(path)
            if digest in seen and seen[digest]!=split:
                raise ValueError(f'Rò rỉ dữ liệu: ảnh giống hệt nhau xuất hiện trong {split} và {seen[digest]}.')
            seen[digest]=split
            manifest.append({'path':str(Path(path).relative_to(root)), 'label':CLASSES[label], 'split':split, 'sha256':digest})
    return splits,manifest

def make_dataset(files,size,batch,training=False,seed=42):
    import tensorflow as tf
    def generator():
        for path,label in files:
            image=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None:raise ValueError(f'Không đọc được ảnh: {path}')
            yield rgb_input(image,size), np.array([label],dtype=np.float32)
    dataset=tf.data.Dataset.from_generator(generator,output_signature=(tf.TensorSpec((size,size,3),tf.float32),tf.TensorSpec((1,),tf.float32)))
    dataset=dataset.apply(tf.data.experimental.assert_cardinality(len(files)))
    if training:dataset=dataset.shuffle(min(len(files),4096),seed=seed,reshuffle_each_iteration=True)
    options=tf.data.Options();options.threading.private_threadpool_size=2
    return dataset.batch(batch).with_options(options).prefetch(1)
