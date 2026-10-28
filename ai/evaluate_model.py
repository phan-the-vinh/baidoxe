"""Chi ghi metrics sau khi du doan that tren tap test du ca hai nhan."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from ai.data import scan_split,make_dataset,sha256_file,inspect_dataset

def evaluate(model_path,test_dir,output_dir,manifest=None):
    import tensorflow as tf
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from sklearn.metrics import accuracy_score,precision_score,recall_score,f1_score,confusion_matrix,log_loss,classification_report
    model_path=Path(model_path)
    if not model_path.exists():raise ValueError('Chưa có model. Hãy huấn luyện trước khi đánh giá.')
    files=scan_split(test_dir)
    model=tf.keras.models.load_model(model_path,compile=False)
    size=int(model.input_shape[1]);dataset=make_dataset(files,size,32)
    probs=np.concatenate([model(x,training=False).numpy().ravel() for x,_ in dataset])
    truth=np.array([label for _,label in files]);prediction=(probs>=0.5).astype(int)
    matrix=confusion_matrix(truth,prediction,labels=[0,1])
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    metrics={'accuracy':float(accuracy_score(truth,prediction)),
        'precision':float(precision_score(truth,prediction,zero_division=0)),
        'recall':float(recall_score(truth,prediction,zero_division=0)),
        'f1':float(f1_score(truth,prediction,zero_division=0)),
        'loss':float(log_loss(truth,np.clip(probs,1e-7,1-1e-7),labels=[0,1])),
        'samples':len(files),'class_counts':{'empty':int((truth==0).sum()),'occupied':int((truth==1).sum())},
        'positive_class':'occupied','threshold':0.5,'model_sha256':sha256_file(model_path),
        'evaluated_at':datetime.now(timezone.utc).isoformat(),
        'test_path':str(Path(test_dir).resolve()),'confusion_matrix':matrix.tolist(),
        'classification_report':classification_report(truth,prediction,labels=[0,1],target_names=['empty','occupied'],output_dict=True,zero_division=0)}
    fig,ax=plt.subplots(figsize=(5,4));ax.imshow(matrix,cmap='Greens')
    for i in range(2):
        for j in range(2):ax.text(j,i,str(matrix[i,j]),ha='center',va='center',fontsize=18)
    ax.set(xticks=[0,1],xticklabels=['EMPTY','OCCUPIED'],yticks=[0,1],yticklabels=['EMPTY','OCCUPIED'],xlabel='Predicted',ylabel='Actual',title='Test confusion matrix');fig.tight_layout();fig.savefig(output/'confusion_matrix.png',dpi=160);plt.close(fig)
    metrics['test_manifest']=[{'path':str(Path(p).resolve()),'label':int(y),'sha256':sha256_file(p)} for p,y in files]
    (output/'metrics.json').write_text(json.dumps(metrics,indent=2,ensure_ascii=False),encoding='utf-8')
    return metrics

def main():
    parser=argparse.ArgumentParser(description='Đánh giá CNN trên test set độc lập')
    parser.add_argument('--model',default='ai_models/parking_classifier.keras')
    parser.add_argument('--dataset',default='dataset/parking')
    parser.add_argument('--output',default='ai/results')
    args=parser.parse_args()
    # Kiem tra anh trung giua train/validation/test ca khi evaluate doc lap.
    inspect_dataset(Path(args.dataset))
    metrics=evaluate(args.model,Path(args.dataset)/'test',args.output)
    print(json.dumps({k:metrics[k] for k in ['accuracy','precision','recall','f1','loss','samples']},indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,FileNotFoundError,ImportError) as exc:raise SystemExit(str(exc))
