# calibrate/

Pin pitch landmarks on a picture and save them for the calibrators. A fixed
camera's frame gets a homography fitted live, with the pitch drawn back over
it. A panning camera's panorama is solved in Python, because a stitched
panorama is not one plane projection.

The saved file carries the pins under both calibrators' names
(`landmarks.js`), so it feeds `python -m pitchiq calibrate` or the panning
path alike. It runs with the rest of the site (`shared/README.md`, "Local
development") and needs no account: nothing is written to Firestore.
