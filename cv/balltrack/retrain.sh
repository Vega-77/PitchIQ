#!/bin/sh
# Two label sets from the croplab db dump in labdb5/, trained side by side,
# then each scored on the wm test clips in turn (clsscore overwrites
# wmNNcls.npz, so the scoring is sequential).
#   A  pseudo-labels (ball + notball) + human
#   B  pseudo notball only + human    (pseudo "ball" was 20% wrong)
S2="$(cd "$(dirname "$0")" && pwd)"
PY="C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe"
cd "$S2" || exit 1
TAGS=tr00,tr01,tr02,tr03,tr04,tr05,tr06,tr07,tr08,tr09,tr10,tr11,tr12,tr13,tr14,tr15,tr16
WM=$(ls wm[0-9][0-9].npz | sed 's/\.npz//' | tr '\n' ' ')
"$PY" -c "
import json,glob
P=json.load(open('pseudo_labels.json'))
H=[dict(tag=d['tag'],f=d['f'],j=d['j'],label=d['label']) for d in (json.load(open(p)) for p in glob.glob('labdb5/crops/*.json')) if d['label']!='unsure']
json.dump(P+H,open('labels_A.json','w'))
json.dump([r for r in P if r['label']!='ball']+H,open('labels_B.json','w'))
print('human',len(H),'pseudo',len(P))
" || exit 1
"$PY" train_cls.py train --tags $TAGS --labels labels_A.json --out cls_A.pt > train_A.log 2>&1 &
"$PY" train_cls.py train --tags $TAGS --labels labels_B.json --out cls_B.pt > train_B.log 2>&1 &
wait
for v in A B; do
  [ -f cls_$v.pt ] || { echo "no cls_$v.pt"; continue; }
  "$PY" clsscore.py cls_$v.pt $WM > clsscore_$v.log 2>&1 || { echo "score $v failed"; continue; }
  mkdir -p cls_$v; cp wm[0-9][0-9]cls.npz cls_$v/
  "$PY" wm_cls.py > wm_cls_$v.log 2>&1
  echo "=== $v"; cat wm_cls_$v.log
done
echo RETRAIN DONE
