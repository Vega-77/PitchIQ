#!/bin/bash
# 2026-11-11: seed repeats of the 3-frame stack, stack + own-game pseudo-labels, 5-frame stack. One GPU job at a time.
G=C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain/pitchiq/experiments/ballpush/selftrain/go.sh
L=C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/logs
TRAIN=train2.py bash $G k1_s10 global --stack 1 --pseudo strict --seed-offset 10 > $L/go_k1_s10.log 2>&1
TRAIN=train2.py bash $G k1_s20 global --stack 1 --pseudo strict --seed-offset 20 > $L/go_k1_s20.log 2>&1
TRAIN=train2.py bash $G ko1 global --stack 1 --pseudo strict+own > $L/go_ko1.log 2>&1
TRAIN=train2.py bash $G ko1_s10 global --stack 1 --pseudo strict+own --seed-offset 10 > $L/go_ko1_s10.log 2>&1
TRAIN=train2.py bash $G k2 global --stack 2 --pseudo strict > $L/go_k2.log 2>&1
echo QUEUE3 DONE > $L/queue3.done
