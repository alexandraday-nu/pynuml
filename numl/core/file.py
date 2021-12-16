import h5py, numpy as np, pandas as pd
from mpi4py import MPI
import sys
import enum

@enum.unique
class Partition(enum.Enum):
  evt_id_based = 0
  evt_amnt_based = 1
  particle_based = 2

# class process_label(enum.Enum):
CoulombScat           = 0
CoupledTransportation = 1
Decay                 = 2
FastScintillation     = 3
He3Inelastic          = 4
StepLimiter           = 5
alphaInelastic        = 6
annihil               = 7
compt                 = 8
conv                  = 9
dInelastic            = 10
eBrem                 = 11
eIoni                 = 12
electronNuclear       = 13
hBertiniCaptureAtRest = 14
hBrems                = 15
hIoni                 = 16
hadElastic            = 17
kaonPInelastic        = 18
kaonMInelastic        = 19
kaon0LInelastic       = 20
kaon0SInelastic       = 21
lambdaInelastic       = 22
muBrems               = 23
muIoni                = 24
muMinusCaptureAtRest  = 25
muPairProd            = 26
muPlusCaptureAtRest   = 27
muonNuclear           = 28
nCapture              = 29
neutronInelastic      = 30
phot                  = 31
photonNuclear         = 32
piPlusInelastic       = 33
piMinusInelastic      = 34
positronNuclear       = 35
primary               = 36
protonInelastic       = 37
sigmaPlusInelastic    = 38
sigmaMinusInelastic   = 39
tInelastic            = 40

class NuMLFile:
  def __init__(self, file):
    self._filename = file
    self._colmap = {
      "event_table": {
        "nu_dir": [ "nu_dir_x", "nu_dir_y", "nu_dir_z" ],
      },
      "particle_table": {
        "start_position": [ "start_position_x", "start_position_y", "start_position_z" ],
        "end_position": [ "end_position_x", "end_position_y", "end_position_z" ],
      },
      "hit_table": {},
      "spacepoint_table": {
        "hit_id": [ "hit_id_u", "hit_id_v", "hit_id_y" ],
        "position": [ "position_x", "position_y", "position_z" ],
      },
      "edep_table": {}
    }

    # open the input HDF5 file in parallel
    self._fd = h5py.File(self._filename, "r", driver='mpio', comm=MPI.COMM_WORLD)

    # obtain metadata of dataset "event_table/event_id", later the dataset will
    # be read into self._index as a numpy array in data_partition()
    self._index = self._fd.get("event_table/event_id")
    self._num_events = self._index.shape[0]

    # self._groups is a python list, each member is a 2-element list consisting
    # of a group name, and a python list of dataset names
    self._groups = []

    # a python dictionary storing a sequence-count dataset in each group, keys
    # are group names, values are the sequence-count dataset subarrays assigned
    # to this process
    self._seq_cnt = {}
    self._evt_seq = {}

    self._whole_seq_cnt = {}
    self._whole_seq     = {}

    self._use_seq_cnt = True

    # a python nested dictionary storing datasets of each group read from the
    # input file. keys of self._data are group names, values are python
    # dictionaries, each has names of dataset in that group as keys, and values
    # storing dataset subarrays
    self._data = {}

    # starting array index of event_table/event_id assigned to this process
    self._my_start = -1

    # number of array elements of event_table/event_id assigned to this process
    self._my_count = -1

  def __len__(self):
    # inquire the number of unique event IDs in the input file
    return self._num_events

  def __str__(self):
    with h5py.File(self._filename, "r") as f:
      ret = ""
      for k1 in self._file.keys():
        ret += f"{k1}:\n"
        for k2 in self._file[k1].keys(): ret += f"  {k2}"
        ret += "\n"
      return ret

  def add_group(self, group, keys=[]):
    if not keys:
      # retrieve all the dataset names of the group
      keys = list(self._fd[group].keys())
      # dataset event_id is not needed
      if "event_id" in keys: keys.remove("event_id")
      if "event_id.seq" in keys: keys.remove("event_id.seq")
      if "event_id.seq_cnt" in keys: keys.remove("event_id.seq_cnt")
    self._groups.append([ group, keys ])

  def keys(self):
    # with h5py.File(self._filename, "r") as f:
    #   return f.keys()
    return self._fd.keys()

  def _cols(self, group, key):
    if key == "event_id": return [ "run", "subrun", "event" ]
    if key in self._colmap[group].keys(): return self._colmap[group][key]
    else: return [key]

  def get_dataframe(self, group, keys=[]):
    with h5py.File(self._filename, "r") as f:
      if not keys:
        keys = list(f[group].keys())
        if "event_id.seq" in keys: keys.remove("event_id.seq")
        if "event_id.seq_cnt" in keys: keys.remove("event_id.seq_cnt")
      dfs = [ pd.DataFrame(np.array(f[group][key]), columns=self._cols(group, key)) for key in keys ]
      return pd.concat(dfs, axis="columns").set_index(["run","subrun","event"])

  def index(self, idx):
    """get the index for a given row"""
    #with h5py.File(self._filename, "r") as f:
      #return f["event_table/event_id"][idx]
    return self._index[idx - self._my_start]

  def __getitem__(self, idx):
    """load a single event from file"""
    with h5py.File(self._filename, "r") as f:
      index = f["event_table/event_id"][idx]
      ret = { "index": index }

      for group, datasets in self._groups:
        m = (f[group]["event_id"][()] == index).all(axis=1)
        if not datasets: datasets = list(f[group].keys())
        def slice(g, d, m):
          n = f[g][d].shape[1]
          m = m[:,None].repeat(n, axis=1)
          return pd.DataFrame(f[g][d][m].reshape([-1,n]), columns=self._cols(g,d))
        dfs = [ slice(group, dataset, m) for dataset in datasets ]
        ret[group] = pd.concat(dfs, axis="columns")
      return ret

  def __del__(self):
    self._fd.close()

  def read_seq(self):
    for group, datasets in self._groups:
      try:
        # read an HDF5 dataset into a numpy array
        buf = self._fd.get(group+"/event_id.seq")
        self._whole_seq[group] = np.array(buf)
      except KeyError:
        print("Error: dataset",group+"/event_id.seq does not exist")
        sys.stdout.flush()
        comm.Abort(1)

  def read_seq_cnt(self):
    for group, datasets in self._groups:
      try:
        # read an HDF5 dataset into a numpy array
        buf = self._fd.get(group+"/event_id.seq_cnt")
        self._whole_seq_cnt[group] = np.array(buf)
      except KeyError:
        print("Error: dataset",group+"/event_id.seq_cnt does not exist")
        sys.stdout.flush()
        comm.Abort(1)

  def data_partition(self, use_seq=False, evt_partition=Partition.particle_based):
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    nprocs = comm.Get_size()
    starts = np.zeros(nprocs, dtype=np.int)
    counts = np.zeros(nprocs, dtype=np.int)

    self._use_seq_cnt = not use_seq
    self._evt_partition = evt_partition

    if rank == 0:
      if self._use_seq_cnt:
        self.read_seq_cnt()
      else:
        self.read_seq()

      num_events = self._num_events

      if evt_partition == Partition.evt_id_based:
        # Below implements event ID based partitioning, which
        # calculates the start and count of evt.seq id for each process
        _count = num_events // nprocs
        for j in range(num_events % nprocs):
          starts[j] = _count * j + j
          counts[j] = _count + 1

        for j in range(num_events % nprocs, nprocs):
          starts[j] = _count * j + num_events % nprocs
          counts[j] = _count

      elif evt_partition == Partition.evt_amnt_based:
        # event amount based partitioning, which calculates event sizes
        # across all groups. Note it is possible multiple consecutive rows
        # a dataset have the same event ID. It is also possible some event
        # IDs contain no data. First, we accumulate numbers of events
        # across all groups
        evt_size = np.zeros(num_events, dtype=np.int)
        if self._use_seq_cnt:
          for group, datasets in self._groups:
            seq_cnt = self._whole_seq_cnt[group]
            num_datasets = len(datasets)
            for i in range(seq_cnt.shape[0]):
              evt_size[seq_cnt[i, 0]] += seq_cnt[i, 1] * num_datasets
        else:
          for group, datasets in self._groups:
            seq = self._whole_seq[group]
            for i in range(seq.shape[0]):
              evt_size[seq[i, 0]] += 1

        # now we have collected the number of events per event ID across all groups
        total_evt_num = np.sum(evt_size)
        avg_evt_num = total_evt_num // nprocs
        avg_evt = total_evt_num // num_events / 2

        # assign ranges of event IDs to individual processes
        acc_evt_num = 0
        rank_id = 0
        for j in range(num_events):
          if rank_id == nprocs - 1: break
          if acc_evt_num + evt_size[j] >= avg_evt_num:
            remain_l = avg_evt_num - acc_evt_num
            remain_r = evt_size[j] - remain_l
            if remain_l > remain_r and remain_l > avg_evt:
              # assign event j to rank_id
              counts[rank_id] += 1
              acc_evt_num = 0
            else:
              # assign event j to rank_id+1
              counts[rank_id+1] = 1
              acc_evt_num = evt_size[j]
            # done with rank_id i
            rank_id += 1
            starts[rank_id] = starts[rank_id-1] + counts[rank_id-1]
          else:
            counts[rank_id] += 1
            acc_evt_num += evt_size[j]
        counts[nprocs-1] += num_events - j

      elif evt_partition == Partition.particle_based:
        # use event amounts in the particle_table only to partition events
        seq_cnt = self._whole_seq_cnt['particle_table']
        total_evt_num = np.sum(seq_cnt[:,1])
        avg_evt_num = total_evt_num // nprocs
        avg_evt = total_evt_num // seq_cnt.shape[0] / 2

        starts[0] = seq_cnt[0,0]
        acc_evt_num = 0
        rank_id = 0
        for j in range(seq_cnt.shape[0]):
          if rank_id == nprocs - 1: break
          if acc_evt_num + seq_cnt[j,1] >= avg_evt_num:
            remain_l = avg_evt_num - acc_evt_num
            remain_r = seq_cnt[j,1] - remain_l
            # if remain_r > remain_l:
            if remain_l > remain_r and remain_l > avg_evt:
              # assign event j to rank_id
              counts[rank_id] = seq_cnt[j+1, 0] - starts[rank_id]
              starts[rank_id+1] = seq_cnt[j+1, 0]
              acc_evt_num = 0
            else:
              # assign event j to rank_id+1
              counts[rank_id] = seq_cnt[j, 0] - starts[rank_id]
              starts[rank_id+1] = seq_cnt[j, 0]
              acc_evt_num = seq_cnt[j, 1]
            # done with rank_id
            rank_id += 1
          else:
            acc_evt_num += seq_cnt[j, 1]

        counts[nprocs-1] = num_events - starts[nprocs-1]

    # All processes participate the collective communication, scatter.
    # Root distributes start and count to all processes. Note only root process
    # uses starts and counts.
    start_count = np.empty([nprocs, 2], dtype=np.int)
    start_count[:, 0] = starts[:]
    start_count[:, 1] = counts[:]
    recvbuf = np.empty(2, dtype=np.int)
    comm.Scatter(start_count, recvbuf, root=0)
    self._my_start = recvbuf[0]
    self._my_count = recvbuf[1]

    # This process is assigned event IDs of range from self._my_start to
    # (self._my_start + self._my_count - 1)
    # print("my_start=",self._my_start," my_count=",self._my_count);

    # each process reads its share of dataset "event_table/event_id" and
    # stores it in an numpy array
    self._index = np.array(self._index[self._my_start : self._my_start + self._my_count, :])

    return starts, counts

  def binary_search_min(self, key, base, nmemb):
    low = 0
    high = nmemb
    while low != high:
        mid = (low + high) // 2
        if base[mid] < key:
            low = mid + 1
        else:
            high = mid
    return low

  def binary_search_max(self, key, base, nmemb):
    low = 0
    high = nmemb
    while low != high:
        mid = (low + high) // 2
        if base[mid] <= key:
            low = mid + 1
        else:
            high = mid
    return (low - 1)

  def calc_bound_seq(self, starts, counts, group):
    # return the lower and upper array indices of subarray assigned to this
    # process, using the partition sequence dataset

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    nprocs = comm.Get_size()

    displ  = np.zeros([nprocs], dtype=np.int)
    count  = np.zeros([nprocs], dtype=np.int)
    bounds = np.zeros([nprocs, 2], dtype=np.int)

    all_evt_seq = None
    if rank == 0:
      # root reads the entire dataset event_id.seq, if not already
      if not self._whole_seq: self.read_seq()

      all_evt_seq = self._whole_seq[group]
      dim = len(all_evt_seq)

      # calculate displ, count to be used in scatterV for all processes
      for i in range(nprocs):
        if counts[i] == 0: continue
        end = starts[i] + counts[i] - 1
        bounds[i, 0] = self.binary_search_min(starts[i], all_evt_seq, dim)
        bounds[i, 1] = self.binary_search_max(end,       all_evt_seq, dim)
        displ[i] = bounds[i, 0]
        count[i] = bounds[i, 1] - bounds[i, 0] + 1

    lower_upper = np.empty([2], dtype=np.int)

    # root distributes start and end indices to all processes
    comm.Scatter(bounds, lower_upper, root=0)

    # this process is assigned array indices from lower to upper
    # print("group=",group," lower=",lower," upper=",upper," count=",upper-lower)

    lower = 0
    upper = 0
    if self._my_count > 0:
      lower = lower_upper[0]
      upper = lower_upper[1] + 1

    # root scatters the subarray of evt_seq to all processes
    self._evt_seq[group] = np.zeros(upper - lower, dtype=np.int64)
    comm.Scatterv([all_evt_seq, count, displ, MPI.LONG_LONG], self._evt_seq[group], root=0)

    return lower, upper

  def calc_bound_seq_cnt(self, starts, counts, group):
    # return the lower and upper array indices of subarray assigned to this
    # process, using the partition sequence-count dataset

    comm   = MPI.COMM_WORLD
    rank   = comm.Get_rank()
    nprocs = comm.Get_size()

    displ   = np.zeros([nprocs], dtype=np.int)
    count   = np.zeros([nprocs], dtype=np.int)
    seq_cnt = np.zeros([nprocs, 2], dtype=np.int)

    all_seq_cnt = None
    if rank == 0:
      # root reads the entire dataset event_id.seq_cnt, if not already
      if not self._whole_seq_cnt: self.read_seq_cnt()

      all_seq_cnt = self._whole_seq_cnt[group]
      dim = len(all_seq_cnt)

      # calculate displ, count for all processes to be used in scatterV
      recv_rank = 0  # receiver rank
      displ[recv_rank] = 0
      seq_cnt[recv_rank, 0] = 0
      seq_end = starts[recv_rank] + counts[recv_rank]
      seq_id = 0
      for i in range(dim):
        if all_seq_cnt[i, 0] >= seq_end :
          seq_cnt[recv_rank, 1] = i - displ[recv_rank]
          recv_rank += 1  # move on to the next receiver rank
          seq_end = starts[recv_rank] + counts[recv_rank]
          displ[recv_rank] = i
          seq_cnt[recv_rank, 0] = seq_id
        seq_id += all_seq_cnt[i, 1]

      # last receiver rank
      seq_cnt[recv_rank, 1] = dim - displ[recv_rank]

      # print("starts=",starts," counts=",counts," displ=",displ," count=",count," seq_cnt=",seq_cnt )
      displ[:] *= 2
      count[:] = seq_cnt[:, 1] * 2

    # root distributes seq_cnt to all processes
    my_seq_cnt = np.empty([2], dtype=np.int)
    comm.Scatter(seq_cnt, my_seq_cnt, root=0)

    # this process is assigned array indices from lower to upper
    # print("group=",group," lower=",lower," upper=",upper," count=",upper-lower)

    # self._seq_cnt[group][:, 0] is the event ID
    # self._seq_cnt[group][:, 1] is the number of elements
    self._seq_cnt[group] = np.empty([my_seq_cnt[1], 2], dtype=np.int64)

    # root scatters the subarray of evt_seq to all processes
    comm.Scatterv([all_seq_cnt, count, displ, MPI.LONG_LONG], self._seq_cnt[group], root=0)

    lower = 0
    upper = 0
    if self._my_count > 0:
      lower = my_seq_cnt[0]
      upper = my_seq_cnt[0] + np.sum(self._seq_cnt[group][:, 1])

    return lower, upper

  def read_data(self, starts, counts, profile=False):
    # Parallel read dataset subarrays assigned to this process ranging from
    # array index of self._my_start to (self._my_start + self._my_count - 1)
    if profile:
      bnd_time = 0
      rds_time = 0

    for group, datasets in self._groups:
      if profile: time_s = MPI.Wtime()

      if self._use_seq_cnt:
        # use evt_id.seq_cnt to calculate subarray boundaries
        lower, upper = self.calc_bound_seq_cnt(starts, counts, group)
      else:
        # use evt_id.seq to calculate subarray boundaries
        lower, upper = self.calc_bound_seq(starts, counts, group)

      if profile:
        time_e = MPI.Wtime()
        bnd_time += time_e - time_s
        time_s = time_e

      # Iterate through all the datasets and read the subarray from index lower
      # to upper and store it into a dictionary with the names of group and
      # dataset as the key.
      self._data[group] = {}
      for dataset in datasets:
        dest = dataset
        if dataset == "start_process_id": dest = "start_process"
        elif dataset == "end_process_id": dest = "end_process"
        # read subarray into a numpy array using independent mode (HDF5 default)
        self._data[group][dest] = np.array(self._fd[group][dataset][lower : upper])
        # with self._fd[group][dataset].collective:  # read each dataset collectively
          # self._data[group][dataset] = self._fd[group][dataset][lower : upper]

      if profile:
        time_e = MPI.Wtime()
        rds_time += time_e - time_s
        time_s = time_e

    if profile:
      rank   = MPI.COMM_WORLD.Get_rank()
      nprocs = MPI.COMM_WORLD.Get_size()

      total_t = np.array([bnd_time, rds_time])
      max_total_t = np.zeros(2)
      MPI.COMM_WORLD.Reduce(total_t, max_total_t, op=MPI.MAX, root = 0)
      min_total_t = np.zeros(2)
      MPI.COMM_WORLD.Reduce(total_t, min_total_t, op=MPI.MIN, root = 0)
      if rank == 0:
        print("---- Timing break down of the file read phase (in seconds) -------")
        if self._use_seq_cnt:
          print("Use event_id.seq_cnt to calculate subarray boundaries")
        else:
          print("Use event_id.seq to calculate subarray boundaries")
        print("calc boundaries time MAX=%8.2f  MIN=%8.2f" % (max_total_t[0], min_total_t[0]))
        print("read datasets   time MAX=%8.2f  MIN=%8.2f" % (max_total_t[1], min_total_t[1]))
        print("(MAX and MIN timings are among %d processes)" % nprocs)

  def build_evt(self, start=None, count=None):
    # This process is responsible for event IDs from start to (start+count-1).
    # All data of the same event ID will be used to create a graph.
    # This function collects all data based on event_id.seq or event_id.seq_cnt
    # into a python list containing Pandas DataFrames, one for a unique event
    # ID.
    ret_list = []

    if start is None: start = self._my_start
    if count is None: count = self._my_count

    num_miss = 0

    # track the latest used index per group
    idx_grp = dict.fromkeys(self._data.keys(), 0)

    # accumulate starting array index per group
    idx_start = dict.fromkeys(self._data.keys(), 0)

    # Iterate through assigned event IDs
    for idx in range(int(start), int(start+count)):
      # check if idx is missing in all groups
      is_missing = True
      if self._use_seq_cnt:
        for group in self._data.keys():
          if idx_grp[group] >= len(self._seq_cnt[group][:, 0]):
            continue
          if idx == self._seq_cnt[group][idx_grp[group], 0]:
            is_missing = False
            break
      else:
        for group in self._data.keys():
          dim = len(self._evt_seq[group])
          lower = self.binary_search_min(idx, self._evt_seq[group], dim)
          upper = self.binary_search_max(idx, self._evt_seq[group], dim) + 1
          if lower < upper:
            is_missing = False
            break

      # this idx is missing in all groups
      if is_missing:
        num_miss += 1
        continue

      # for each event seq ID, create a dictionary, ret
      #   first item: key is "index" and value is the event seq ID
      #   remaining items: key is group name and value is a Pandas DataFrame
      #   containing the dataset subarray in this group with the event ID, idx
      ret = { "index": idx }

      # Iterate through all groups
      for group in self._data.keys():

        if self._use_seq_cnt:
          # self._seq_cnt[group][:, 0] is the event ID
          # self._seq_cnt[group][:, 1] is the number of elements

          if idx_grp[group] >= len(self._seq_cnt[group][:, 0]) or idx < self._seq_cnt[group][idx_grp[group], 0]:
            # idx is missing from this group but may not in other groups
            # create an empty Pandas DataFrame
            dfs = []
            for dataset in self._data[group].keys():
              data_dataframe = pd.DataFrame(columns=self._cols(group, dataset))
              dfs.append(data_dataframe)
            ret[group] = pd.concat(dfs, axis="columns")
            # print("xxx",group," missing idx=",idx," _seq_cnt[0]=",self._seq_cnt[group][idx_grp[group], 0])
            continue

          lower = idx_start[group]
          upper = self._seq_cnt[group][idx_grp[group], 1] + lower

          idx_start[group] += self._seq_cnt[group][idx_grp[group], 1]
          idx_grp[group] += 1

        else:
          # Note self._evt_seq stores event ID values and is already sorted in
          # an increasing order
          dim = len(self._evt_seq[group])

          # Find the local start and end row indices for this event ID, idx
          lower = self.binary_search_min(idx, self._evt_seq[group], dim)
          upper = self.binary_search_max(idx, self._evt_seq[group], dim) + 1
          # print("For idx=",idx, " lower=",lower, " upper=",upper)

        # dfs is a python list containing Pandas DataFrame objects
        dfs = []
        for dataset in self._data[group].keys():
          if lower >= upper:
            # idx is missing from the dataset event_id.seq
            # In this case, create an empty numpy array
            data = np.array([])
          else:
            # array elements from lower to upper of this dataset have the
            # event ID == idx
            data = self._data[group][dataset][lower : upper]

          # create a Pandas DataFrame to store the numpy array
          data_dataframe = pd.DataFrame(data, columns=self._cols(group, dataset))

          dfs.append(data_dataframe)

        # concate into the dictionary "ret" with group names as keys
        ret[group] = pd.concat(dfs, axis="columns")

      # Add all dictionaries "ret" into a list.
      # Each of them corresponds to the data of one single event ID
      ret_list.append(ret)

    # print("start=",start," count=",count," num miss IDs=",num_miss)
    return ret_list

