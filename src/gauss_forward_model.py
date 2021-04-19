import numpy as np
import numba as nb
import math
import coords



def make_gauss_2d(xv,yv,mu,sigma):
  g = np.exp(-( (xv-mu[0])**2 + (yv-mu[1])**2)  /(2*sigma**2) )
  return(g)

def make_map_3d(atoms,xyz,N,sigma):
  C = 1/np.sqrt(2*np.pi*sigma**2)
  diff = xyz.reshape(-1,3,1) - atoms[:3,:].reshape(1,3,-1)
  a = -1/(2*sigma**2)
  map_3d = (C**3*np.exp(a*((diff**2).sum(1))).sum(1).reshape(N,N,N))
  return(map_3d)

# TODO: see if faster to copy over scalar vs vectorized scalar
# float32 vs 64
# circular patch vs box. change equation for idx
# precompute rotated idx and rotated atoms for each rotation?
# copy data over in rotation matrix, or as quaternions for compresion and then compute 3x3 R on the fly
# how big does patch need to be? 3 sigma overkill?
@nb.guvectorize([(nb.float64[:,:,:],nb.float64[:,:],nb.int64[:],nb.float64[:,:],nb.int64[:],nb.int64[:],nb.float64[:],nb.float64[:,:])], 
  '(n_proj,r2,r3),(N2,r2),(n_atoms),(r3,n_atoms),(n_atoms),(n_atoms),(n_atoms)->(n_proj,N2)',
  nopython=True,target='cuda')
def precompute_idx_ntrunc_rot_gpu(Rs,xy,N,atoms,idx,n_trunc,sigma,g_2d):
  '''

  '''
  for proj_idx in range(Rs.shape[0]):
    R = Rs[proj_idx,:,:]

    for i in range(idx.shape[0]): # loop over atoms
      nt_ = (n_trunc[i]-1)//2
      a = -1/(2*sigma[i]*sigma[i]) # sigma in pixel units

      x = atoms[0,i]
      y = atoms[1,i]
      z = atoms[2,i]

      # code dot product explicitly
      x_rot = R[0,0]*x + R[0,1]*y + R[0,2]*z
      y_rot = R[1,0]*x + R[1,1]*y + R[1,2]*z

      X = round(x_rot) + N[i]//2
      Y = round(y_rot) + N[i]//2
      idx[i] = X+N[i]*Y # this has to match with xy (vs yx). check xy[[0,1],:] = xy[[1,0],:] if problems

      for x_idx in range(idx[i]-nt_,idx[i]+nt_+1,1): 
        for y_idx in range(-nt_,+nt_+1,1):
          xy_idx = x_idx + y_idx*N[i]
          diffx = xy[xy_idx,0] - x_rot  # atom locations in pixel units
          diffy = xy[xy_idx,1] - y_rot 
          d2i = diffx*diffx+diffy*diffy
          gi = math.exp(a*d2i)
          g_2d[proj_idx,xy_idx] += gi # TODO double check no racing condition.

def idx_from_atoms(atoms,N):
  X = np.round(atoms[0]).astype(np.int32) + N//2
  Y = np.round(atoms[1]).astype(np.int32) + N//2
  idx = X+N*Y
  return(idx)

def make_proj_gpu(atoms,xy,N,n_proj,sigma,n_trunc=None,Rs=None,method='precompute_idx_ntrunc_rot_gpu',random_seed=0):
  '''
  rotate point cloud and project onto 2D grid assumig gaussians
  initialization of g_2d and copy steps onto GPU are bottleneck. actual computation of g_2d_gpu stays ms for 1-100k rotations on 128^2 grid and 10-100k atoms. hit memory issues
  '''
  
  g_2d = np.zeros((n_proj,N*N),dtype=np.float64) # TODO: try to initialize this large array of zeros on host
  if n_trunc is None: n_trunc = np.int64(6*sigma)
  
  if Rs is not None:
    if random_seed is not None: np.random.seed(random_seed)
    Rs = coords.uniform_rotations(n_proj)

  assert Rs.shape == (n_proj,3,3)

  idx = idx_from_atoms(atoms,N)

  # copy data to gpu
  # TODO: see if faster to copy over single scalar (N,n_trunc,sigma) vs vectorized scalar
  Rs_gpu = nb.cuda.to_device(Rs[:,:2,:].copy()) # only need x and y part since will ignore final rotated z coordinate
    # have to .copy because can only pass in subset on first axis, otherwise error that non-contiguous
  N_gpu = nb.cuda.to_device(N*np.ones(atoms.shape[1]).astype(np.int64))
  n_trunc_gpu = nb.cuda.to_device(n_trunc*np.ones(atoms.shape[1]).astype(np.int64))
  sigma_gpu = nb.cuda.to_device(sigma*np.ones(atoms.shape[1]).astype(np.float64))
  xy_gpu = nb.cuda.to_device(xy)
  atoms_gpu = nb.cuda.to_device(atoms[:3,:]) # need z coordinate because will rotate inside
  idx_gpu = nb.cuda.to_device(idx.astype(np.int64))
  g_2d_gpu = nb.cuda.to_device(g_2d) 

  # TODO: for more rotations, put in loop so don't have to redo copy to GPU of other data
  precompute_idx_ntrunc_rot_gpu(Rs_gpu,xy_gpu,N_gpu,atoms_gpu,idx_gpu,n_trunc_gpu,sigma_gpu,g_2d_gpu)
  #TODO look into quick copy to CPU, or doing further GPU computations (taking loss etc)
  return(g_2d_gpu)