"""Bounded cgroup observations and advisory cache release for stopped VM clones."""
import json
import hashlib
import stat
import os
from pathlib import Path
import threading
import time


def numbers(path):
    return {parts[0]:int(parts[1]) for line in path.read_text().splitlines() if len(parts:=line.split())==2}


def pressure(path):
    return {parts[0]:{key:float(value) if key!='total' else int(value) for field in parts[1:] for key,value in [field.split('=')]} for line in path.read_text().splitlines() if (parts:=line.split())}


def snapshot(cgroup,phase):
    row={'monotonic':time.monotonic(),'phase':phase}
    for scope,root in [('outer',cgroup),('supervisor',cgroup/'supervisor')]:
        if not root.exists():continue
        data={}
        for name in ('memory.current','memory.peak','memory.max','memory.swap.current'):
            try:data[name]=int((root/name).read_text())
            except (OSError,ValueError):pass
        for name in ('memory.stat','memory.events.local','memory.events'):
            try:data[name]=numbers(root/name)
            except OSError:pass
        try:data['memory.pressure']=pressure(root/'memory.pressure')
        except OSError:pass
        row[scope]=data
    return row


class MemoryObserver:
    def __init__(self,cgroup,output):
        self.cgroup=cgroup;self.output=output;self.rows=[];self.lock=threading.Lock();self.stop_event=threading.Event()
        self.worker=threading.Thread(target=self.loop,daemon=True)
    def mark(self,phase):
        row=snapshot(self.cgroup,phase)
        with self.lock:self.rows.append(row)
    def loop(self):
        while not self.stop_event.wait(.1):self.mark('periodic')
    def start(self):self.mark('campaign-start');self.worker.start()
    def close(self):
        self.stop_event.set();self.worker.join(timeout=5);self.mark('campaign-end')
        self.output.write_text(json.dumps(self.rows,indent=2)+'\n')


def release_stopped_clones(root, *, stopped):
    """No global drop_caches, no input-file advice, no deletion/truncation.

    A successful fadvise is a request, not proof that every page was evicted.
    The caller must first stop/reap the VMM. Only two exact owned clone paths
    are accepted; symlinks and path escapes fail closed.
    """
    if not stopped:raise ValueError('Refusing cache advice while VMM is alive')
    root=Path(root).resolve(strict=True);results=[]
    for name in ('rootfs.ext4','vmlinux'):
        path=root/name
        if path.is_symlink() or path.resolve(strict=True).parent!=root:
            raise ValueError('Refusing non-owned clone path')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):raise ValueError('Clone must be a regular file')
            os.fsync(fd)
            h=hashlib.sha256()
            while block:=os.read(fd,4*1024*1024):h.update(block)
            os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
            after=os.fstat(fd)
            if (before.st_dev,before.st_ino,before.st_size)!=(after.st_dev,after.st_ino,after.st_size):raise ValueError('Clone identity/size changed during advice')
            results.append({'file':name,'bytes':before.st_size,'fsync':True,'sha256_before_advice':h.hexdigest(),'advice':'POSIX_FADV_DONTNEED','identity_and_size_unchanged':True})
        finally:os.close(fd)
    return results
