import argparse
import json
import sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai.data import inspect_dataset,make_dataset,sha256_file
from ai.model import build_cnn
from ai.evaluate_model import evaluate

def main():
    parser=argparse.ArgumentParser(description='Xây CNN từ trọng số ngẫu nhiên và train trên dataset dự án')
    parser.add_argument('--dataset',default='dataset/parking')
    parser.add_argument('--epochs',type=int,default=40)
    parser.add_argument('--batch-size',type=int,default=32)
    parser.add_argument('--size',type=int,default=96)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--model',default='ai_models/parking_classifier.keras')
    parser.add_argument('--results',default='ai/results')
    parser.add_argument('--split-manifest',help='CSV path,label,group. Chỉ chia khi thư mục đích chưa có ảnh.')
    parser.add_argument('--overwrite',action='store_true',help='Cho phép thay model đã có; hãy sao lưu lần chạy cũ trước')
    args=parser.parse_args()
    if args.epochs<1 or args.batch_size<1 or not 32<=args.size<=256:raise ValueError('Tham số train không hợp lệ.')
    if Path(args.model).exists() and not args.overwrite:raise ValueError('Model đã tồn tại. Đổi --model/--results hoặc dùng --overwrite sau khi sao lưu.')
    if args.split_manifest:
        from tools.prepare_dataset import split_manifest
        split_manifest(args.split_manifest,args.dataset,args.seed)
    # Dataset thieu thi dung truoc khi import TF/tao bat ky metric hay model nao.
    splits,manifest=inspect_dataset(Path(args.dataset))
    import tensorflow as tf
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    tf.keras.utils.set_random_seed(args.seed)
    model=build_cnn(args.size,args.seed)
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),loss='binary_crossentropy',metrics=['accuracy'])
    output=Path(args.results);output.mkdir(parents=True,exist_ok=True)
    target=Path(args.model);target.parent.mkdir(parents=True,exist_ok=True)
    with (output/'model_summary.txt').open('w',encoding='utf-8') as summary_file:
        model.summary(print_fn=lambda s:summary_file.write(s+'\n'))
    (output/'dataset_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (output/'run_config.json').write_text(json.dumps({**vars(args),'started_at':datetime.now(timezone.utc).isoformat(),'initialization':'random','pretrained':False,'class_names':['empty','occupied'],'normalization':'Rescaling(1/255) inside model','split_counts':{k:len(v) for k,v in splits.items()}},indent=2),encoding='utf-8')
    class_counts=[sum(label==i for _,label in splits['train']) for i in [0,1]]
    weights={i:len(splits['train'])/(2*class_counts[i]) for i in [0,1]}
    history=model.fit(make_dataset(splits['train'],args.size,args.batch_size,True,args.seed),
        validation_data=make_dataset(splits['validation'],args.size,args.batch_size),epochs=args.epochs,class_weight=weights,shuffle=False,
        callbacks=[tf.keras.callbacks.ModelCheckpoint(str(target),monitor='val_loss',save_best_only=True),
                   tf.keras.callbacks.EarlyStopping(monitor='val_loss',patience=7,restore_best_weights=True),
                   tf.keras.callbacks.ReduceLROnPlateau(monitor='val_loss',factor=0.5,patience=3),
                   tf.keras.callbacks.CSVLogger(str(output/'training_log.csv'))])
    history_data={k:[float(v) for v in values] for k,values in history.history.items()}
    (output/'history.json').write_text(json.dumps(history_data,indent=2),encoding='utf-8')
    fig,axes=plt.subplots(1,2,figsize=(10,4))
    for ax,key in zip(axes,['accuracy','loss']):
        epochs=range(1,len(history_data[key])+1)
        ax.plot(epochs,history_data[key],label='train');ax.plot(epochs,history_data['val_'+key],label='validation');ax.set(xlabel='Epoch',ylabel=key,title=key.title());ax.legend();ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(output/'training_history.png',dpi=160);plt.close(fig)
    metadata={'class_names':['empty','occupied'],'input_size':args.size,'pretrained':False,
        'model_sha256':sha256_file(target),'trained_at':datetime.now(timezone.utc).isoformat(),
        'normalization':'inside_model_0_255_to_0_1','results_dir':str(output.resolve())}
    target.with_suffix('.metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    metrics=evaluate(target,Path(args.dataset)/'test',output,manifest)
    print('Đã đánh giá thực tế:', {k:metrics[k] for k in ['accuracy','precision','recall','f1','loss']})

if __name__=='__main__':
    try:main()
    except (ValueError,FileNotFoundError,ImportError) as exc:raise SystemExit(str(exc))
