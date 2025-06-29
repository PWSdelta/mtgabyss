import subprocess
import datetime
import os

# Start two worker processes in parallel, outputting to the screen
procs = []
for i in range(2):
    print(f"\n=== Worker {i+1} started at {datetime.datetime.now().isoformat()} ===")
    # Use unbuffered output for immediate printing
    p = subprocess.Popen(["python", "-u", "worker.py"])
    procs.append(p)

for p in procs:
    p.wait()