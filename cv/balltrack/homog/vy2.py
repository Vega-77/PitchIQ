import numpy as np, cv2
d = np.load('vyellow.npy')[350:1600, 0:760]
# local contrast: subtract a wide median so thin lines pop over smear
b = cv2.blur(d.astype(np.float32), (41, 41))
r = np.clip(d - b, 0, None)
r = np.log1p(r * 200)
r = (255 * r / np.percentile(r, 99.7)).clip(0, 255).astype(np.uint8)
cv2.imwrite('vyellow_hp.jpg', r)
# column/row profiles of the high-pass inside field box
print('rows with strong horizontal lines:')
p = r[:, 60:560].mean(1)
for y in np.argsort(p)[::-1][:25]: print(y, round(p[y], 1))
