"""Entrenamiento reproducible en CPU sobre reseñas reales, sin servicios externos.

python scripts/train_review_model.py --limit 24000
No modifica la base. Los textos no se exportan en el artefacto.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score, precision_score, recall_score
from sqlalchemy import select
from app.db.database import SessionLocal
from app.db.real_data import DATA_POLICY, real_review_filter
from app.models import Review
from app.ml.analytics import normalize_text
from app.ml.lexicon import ASPECT_TERMS
from app.ml.trained_reviews import TOPICS, VERSION, ReviewModel, artifact_path, vectorizer

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def weak_labels(text):
    normalized=' '+normalize_text(' '.join(text.split()))+' '
    labels=[]
    for topic in TOPICS[:4]:
        terms=next(terms for aspect,terms in ASPECT_TERMS.items() if aspect.value==topic)
        labels.append(int(any(' '+term+' ' in normalized for term,weight in terms.items() if weight>=.8)))
    labels.append(int(any(' '+term+' ' in normalized for term in
                          ('precio','caro','barato','microtransacciones','pay to win','p2w','dlc','monetizacion','pase de batalla'))))
    labels.append(int(any(' '+term+' ' in normalized for term in
                          ('dificultad','dificil','facil','dificiles','desafiante','desafio','complicado','reto'))))
    return labels

def prepare(rows,limit):
    # Un mismo texto nunca atraviesa particiones ni sobrerrepresenta plantillas.
    seen=set(); unique=[]
    for rid,gid,text,recommended in rows:
        text=text.strip()[:6000]
        key=digest(normalize_text(' '.join(text.split())))
        if len(text)<30 or key in seen:
            continue
        seen.add(key); unique.append((key,int(rid),int(gid),text,int(bool(recommended))))
    unique.sort(key=lambda row:row[0])
    selected=[]; counts=Counter()
    for row in unique:
        if counts[row[2]]>=150:
            continue
        counts[row[2]]+=1; selected.append(row)
        if len(selected)>=limit:
            break
    partitions={'train':[],'validation':[],'test':[]}
    for row in selected:
        bucket=int(digest('game:'+str(row[2]))[:8],16)%10
        name='validation' if bucket==0 else 'test' if bucket==1 else 'train'
        partitions[name].append(row)
    return selected,partitions

def metrics(labels,scores,threshold):
    predicted=scores>=threshold
    return dict(examples=len(labels), positives=int(np.sum(labels)),
                precision=round(float(precision_score(labels,predicted,zero_division=0)),4),
                recall=round(float(recall_score(labels,predicted,zero_division=0)),4),
                f1=round(float(f1_score(labels,predicted,zero_division=0)),4),
                balanced_accuracy=round(float(balanced_accuracy_score(labels,predicted)),4))

def train(rows,path,limit=24000):
    selected,partitions=prepare(rows,limit)
    if any(len(partitions[name])<100 for name in partitions):
        raise ValueError('Se requieren al menos 100 textos únicos en cada partición por juego.')
    vector=vectorizer(); x={}; y={}
    for name,items in partitions.items():
        texts=[row[3] for row in items]
        x[name]=vector.fit_transform(texts) if name=='train' else vector.transform(texts)
        y[name]=np.array([[row[4],*weak_labels(row[3])] for row in items])
    coefficients=[]; intercepts=[]; topics={}; reception={}
    for index,name in enumerate(('reception',*TOPICS)):
        labels=y['train'][:,index]
        if len(np.unique(labels))<2:
            raise ValueError('La etiqueta '+name+' necesita ejemplos de ambas clases.')
        model=LogisticRegression(C=3.,solver='liblinear',class_weight='balanced',max_iter=350,random_state=42)
        model.fit(x['train'],labels)
        coefficients.append(model.coef_[0]); intercepts.append(model.intercept_[0])
        validation=expit(x['validation'] @ model.coef_[0]+model.intercept_[0])
        test=expit(x['test'] @ model.coef_[0]+model.intercept_[0])
        if index==0:
            result=metrics(y['test'][:,index],test,.5)
            majority=np.full(len(test),float(np.mean(labels)>=.5))
            reference=metrics(y['test'][:,index],majority,.5)
            # Activación decidida exclusivamente con validación, no con test.
            val=metrics(y['validation'][:,index],validation,.5)
            reception=dict(eligible=val['balanced_accuracy']>=.60, threshold=.5,
                           target='steam_author_recommendation_not_personal_taste_or_text_sentiment',
                           supervision='real_steam_recommendation_flag', validation=val,
                           test=result, majority_baseline=reference)
        else:
            candidates=[(threshold,metrics(y['validation'][:,index],validation,threshold)) for threshold in (.5,.65,.8,.9)]
            accepted=[item for item in candidates if item[1]['precision']>=.80 and item[1]['f1']>=.40 and item[1]['positives']>=25]
            threshold,val=max(accepted,key=lambda item:item[1]['f1']) if accepted else candidates[-1]
            topics[name]=dict(eligible=bool(accepted), threshold=threshold, supervision='weak_curated_vocabulary',
                              validation_agreement=val, test_agreement=metrics(y['test'][:,index],test,threshold))
        print('Ajustado:',name,flush=True)
    metadata=dict(version=VERSION, algorithm='tfidf_logistic_regression_cpu', trained_at=datetime.now(timezone.utc).isoformat(),
                  seed=42, language='es', source='real_steam_reviews', examples=len(selected),
                  data_policy=DATA_POLICY,
                  games=len({row[2] for row in selected}), vocabulary=len(vector.vocabulary_),
                  dataset_fingerprint=digest('\n'.join(row[0]+':'+str(row[4]) for row in selected)),
                  split='disjoint_game_hash_80_10_10_and_normalized_text_deduplication',
                  partitions={name:dict(examples=len(items),games=len({row[2] for row in items})) for name,items in partitions.items()},
                  reception=reception, topics=topics,
                  limitations=['Temas con etiquetas débiles: las métricas miden acuerdo con el vocabulario, no exactitud humana.',
                               'La recomendación del autor no es el sentimiento de cada aspecto ni el gusto del usuario actual.',
                               'No es un modelo generativo ni utiliza servicios externos.'])
    ReviewModel(vector,np.array(coefficients),np.array(intercepts),metadata).save(path)
    return metadata

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=24000)
    parser.add_argument('--output',type=Path,default=artifact_path())
    args=parser.parse_args()
    if not 1000<=args.limit<=100000:
        parser.error('--limit debe estar entre 1000 y 100000')
    with SessionLocal() as db:
        rows=db.execute(select(Review.id,Review.game_id,Review.content,Review.is_recommended)
                        .where(real_review_filter(),Review.source=='steam',Review.language=='es',Review.is_recommended.is_not(None))
                        .order_by(Review.id)).all()
    result=train(rows,args.output,args.limit)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
