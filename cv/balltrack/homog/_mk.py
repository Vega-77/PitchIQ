import sys, numpy as np
B=np.load(sys.argv[1]); M=np.load(sys.argv[2])
st=B['state']; x=B['x'].copy(); y=B['y'].copy(); g=st!='air'; x[g]=M['x'][g]; y[g]=M['y'][g]
np.savez(sys.argv[3], abs=B['abs'], x=x, y=y, z=B['z'], state=st)
