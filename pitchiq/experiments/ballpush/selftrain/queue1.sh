#!/bin/bash
# the variants after s1, one GPU job at a time
G=C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain/pitchiq/experiments/ballpush/selftrain/go.sh
L=C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/logs
bash $G s1 game > $L/go_s1_game.log 2>&1
bash $G s1_s10 global --pseudo strict --w 0.5 --epochs 12 --seed-offset 10 > $L/go_s1_s10.log 2>&1
bash $G b_s10 global --pseudo none --epochs 30 --seed-offset 10 > $L/go_b_s10.log 2>&1
bash $G own1 global --pseudo strict+own --w 0.5 --epochs 12 > $L/go_own1.log 2>&1
TRAIN=train2.py bash $G c0 global --stack 0 --pseudo strict > $L/go_c0.log 2>&1
TRAIN=train2.py bash $G k1 global --stack 1 --pseudo strict > $L/go_k1.log 2>&1
bash $G s1_w1 global --pseudo strict --w 1.0 --epochs 12 > $L/go_s1_w1.log 2>&1
bash $G s1_w02 global --pseudo strict --w 0.2 --epochs 12 > $L/go_s1_w02.log 2>&1
bash $G s1_s20 global --pseudo strict --w 0.5 --epochs 12 --seed-offset 20 > $L/go_s1_s20.log 2>&1
bash $G b_s20 global --pseudo none --epochs 30 --seed-offset 20 > $L/go_b_s20.log 2>&1
echo QUEUE1 DONE > $L/queue1.done
