import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def split_manifest(csv_path,output,seed=42):
    import numpy as np
    from sklearn.model_selection import GroupShuffleSplit
    from ai.data import EXTENSIONS,scan_split
    source=Path(csv_path).resolve();root=Path(output)
    if any(p.is_file() and p.suffix.lower() in EXTENSIONS for p in root.rglob('*')):
        raise ValueError('Thư mục đích đã có ảnh. Không tự ghi đè/chia lại dữ liệu đã chuẩn bị.')
    with source.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows or not {'path','label','group'}<=set(rows[0]):raise ValueError('CSV cần cột path,label,group.')
    paths=[]
    for row in rows:
        path=Path(row['path']);path=path if path.is_absolute() else source.parent/path
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS or row['label'] not in ('empty','occupied') or not row['group']:
            raise ValueError(f'Dòng CSV không hợp lệ: {row}')
        paths.append(path)
    groups=np.array([r['group'] for r in rows]);labels=np.array([r['label'] for r in rows]);indices=np.arange(len(rows))
    if len(set(groups))<6:raise ValueError('Cần ít nhất 6 nhóm chụp độc lập; nên thu nhiều phiên hơn để chia đáng tin cậy.')
    partitions=None
    for attempt in range(100):
        train_val,test=next(GroupShuffleSplit(n_splits=1,test_size=.15,random_state=seed+attempt).split(indices,labels,groups))
        train_sub,val_sub=next(GroupShuffleSplit(n_splits=1,test_size=.1765,random_state=seed+attempt).split(train_val,labels[train_val],groups[train_val]))
        candidate={'train':train_val[train_sub],'validation':train_val[val_sub],'test':test}
        if all(len(set(labels[ix]))==2 for ix in candidate.values()):partitions=candidate;break
    if partitions is None:raise ValueError('Không chia được cả 2 nhãn ở cả 3 tập. Cần thêm nhóm chụp có đủ nhãn.')
    mapping=[]
    for split,chosen in partitions.items():
        for index in chosen:
            row=rows[index];dest=root/split/row['label']/f'{index:07d}_{paths[index].name}'
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(paths[index],dest)
            mapping.append({**row,'split':split,'destination':str(dest)})
        scan_split(root/split)
    (root/'split_manifest.json').write_text(json.dumps(mapping,indent=2,ensure_ascii=False),encoding='utf-8')
    print({split:len(chosen) for split,chosen in partitions.items()})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--output',default='dataset/parking');parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args()
    try:split_manifest(args.manifest,args.output,args.seed)
    except ValueError as exc:raise SystemExit(str(exc))
