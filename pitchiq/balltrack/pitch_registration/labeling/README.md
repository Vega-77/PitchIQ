# pitch_registration/labeling/

The 3D labs' pages and their answers. All of it is test data: nothing trains on it.

- `air_lab/`, `air_labels/clips.jsonl`: ground or air per frame on vid1
  (`score_air_labels.py` turns it into `../airtruth.npz`).
- `flight_lab/`, `flight_labels/flights.jsonl`: marked flights on vid1
  (`score_flight_labels.py`, `score_dev_clips.py`).
- `flight_lab2/`, `flight_lab2_labels/flights.jsonl`: flights on vid2
  80:00-85:00. `score_flight_lab2.py` reads the same 28 flights from
  `../flightlab2_labels.json`, an earlier export kept because it does.
- `fix_lab/`: the Fix Lab page; its answers are `../../evaluation/fixlab/`.
