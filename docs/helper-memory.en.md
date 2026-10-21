# Why did the helper hit its memory ceiling?

[Português](helper-memory.md) · [Protocol and kernel stacks](vcpu-protocol.en.md) · [Evidence](../evidence/helper-memory/index.json)

The earlier investigation recorded 16,722 helper-local `memory.events.local:max` events without OOM. It did not yet identify what occupied memory or when pressure occurred. We instrumented the shutdown matrix and found **cached disk/kernel copies accumulating between scenarios** as the main observed component. The adopted change reduces this cache after each VM exits, preserving files and existing limits.

## Measurements

The new `vcpu_memory` executor uses the same protocol matrix: real boot, keyboard readiness, two guest loops, task-limit rejection, and shutdown with half a CPU or a two-CPU ceiling, with and without probes. Two unprivileged workers contend on host CPUs 6 and 7. Each campaign repeats the four arms three times, totaling 12 VMs. Campaigns run sequentially under the wrapper's lock.

At phase boundaries and a target 100 ms interval, we collect helper/supervisor `memory.current`, `memory.peak`, `memory.stat`, local/hierarchical events, and PSI. The cgroup peak also covers peaks between samples. Different fields are sequential reads, not atomic snapshots; raw evidence retains timestamps and actual cadence. `memory.events.local:max` counts limit occurrences, not bytes or process deaths. PSI and reclaim counters help establish whether stalls and page recovery occurred.

The measurement window ends after the scenarios and input-hash checks, before serializing the observer's own output file. Writing that file and subsequent export are outside the table's figures.

The helper retained its **768 MiB** ceiling, zero swap, two CPUs, and 256 tasks. Each VM retained **384 MiB** of allowed memory, zero swap, 32 tasks, and 64 descriptors; guest configuration remains 256 MiB. UID/GID, zero effective capabilities, `NoNewPrivs`, seccomp, and jailer namespaces remain checked across all observed runtime threads.

## What stayed resident

Disk hashing already streamed in 4 MiB blocks instead of loading the entire file into Python memory. Each scenario nevertheless copies a 128 MiB disk and approximately 14.15 MiB kernel, retaining them as evidence. Stopping the VM and removing its cgroup does not automatically remove these files' cache from the copying process's cgroup.

In the first campaign, `jailer-lbymgyks`, file cache after the first shutdown was approximately 147 MiB, then 293 MiB after the second. After 12 VMs, it held **686.47 MiB of file cache** and **30.42 MiB of anonymous memory**. The helper hit its ceiling, recording 7,891 `max` events and 254,475 pages scanned by direct reclaim. This reproduced memory pressure without OOM or the historical vCPU panic.

The usage profile, growth per scenario, and measured reduction when acting only on clones support file-cache attribution. We do not equate every `max` event with a heap leak or evidence of the historical panic's cause.

## Adopted change

After confirming that the VMM has stopped and been reaped, the supervisor acts only on **that scenario's `rootfs.ext4` and `vmlinux`**:

1. Opens without truncation, rejecting symlinks and paths outside the expected root.
2. Calls `fsync` to persist data and computes SHA256 in bounded blocks.
3. Requests `POSIX_FADV_DONTNEED` for those files' cache.
4. Preserves both files and records identity, size, hash, and operation duration.

There is no global `drop_caches`, advice on input files, disk deletion, or limit increase. Advice is rejected while the VMM remains alive. It is **advisory**: success does not guarantee that the kernel will evict every page. We therefore measure cgroup effects alongside successful calls.

The default profile and local protocol replay profile now use `helper_cache_policy: "release-stopped-clones"`. The policy runs through shared executor cleanup, including the original jailer controls. Historical profiles without this field retain `retain`, allowing the old behavior to be reproduced. To restore it explicitly, set `helper_cache_policy: "retain"`.

## Before and after

In the final pair, **24/24 VMs exited zero**, with six validated protocol/kernel-stack captures per policy. The earlier pilot contributed 12 additional clean shutdowns outside the pair's figures. All post-boot isolation checks passed and all four original artifacts were unchanged.

| Helper measurement | Retain cache: `jailer-3u6_dwnp` | Release stopped clones: `jailer-qz0gtv58` |
| --- | ---: | ---: |
| VMs / instrumented captures | 12 / 6 | 12 / 6 |
| Memory ceiling | 768 MiB | 768 MiB |
| Memory peak | 768.00 MiB | 262.45 MiB |
| Final total memory | 738.08 MiB | 83.15 MiB |
| Final file cache | 693.68 MiB | 49.39 MiB |
| Final anonymous memory | 30.98 MiB | 30.41 MiB |
| `memory.events.local:max` increase | 7,434 | 0 |
| Pages scanned by direct reclaim | 240,016 | 0 |
| PSI `some` / `full`, total increase | 1.177182 s / 1.095044 s | 0 / 0 |
| Helper OOM / OOM kills | 0 / 0 | 0 / 0 |

The observed peak fell by approximately **65.8%** without increasing the ceiling. In each of the 12 corrected shutdowns, file cache fell approximately **142.15 MiB** between before/after-operation samples. All **24 treated files**, two per VM, were hashed again after the campaign: every hash matched its pre-advice value. Disks remain available for audit.

All **28 automated tests** passed. Default-profile adoption was also checked against the six original jailer controls described below.

The operation has a cost: persistence, hashing, and cache advice took **0.424–0.608 s per VM**, median **0.522 s**, after shutdown. This is outside the measured shutdown interval. Memory reduction is not claimed as a latency improvement.

File cache did not reach zero: binaries, traces, and other output still occupy pages. Other workloads, larger files, or different kernels can behave differently. Shared-page ownership also depends on first touch; these are local comparisons without global cache flushing or a universal benchmark claim.

## Findings remain visible

All three complete campaigns passed, including both campaigns with memory pressure. Their limit events were neither deleted nor relabeled as OOM. The initial campaign is investigative; the `jailer-3u6_dwnp` / `jailer-qz0gtv58` pair uses identical executable source and profiles differing only in cache policy. Evidence includes peaks, PSI, reclaim, before/after release stages, protocol observations, empty stacks, and task-exit read errors. Historical findings remain published in the earlier reports.

Memory pressure was reduced in this sample; **the historical spontaneous panic's cause remains unresolved**. None of the 36 VMs in these three campaigns reproduced that panic.

## Regression and a retained readiness failure

The first original-executor check, [`jailer-ccq7jceq`](../evidence/helper-memory/regression/jailer-ccq7jceq.json), failed before boot: the socket existed, but the second thread was still named `firecracker`, with `Seccomp=0`. Console output recorded API server startup a few milliseconds later. The rejected snapshot, forced cleanup, and hashes remain available.

Readiness now requires observing `Name=fc_api`, `Seccomp=2`, and `NoNewPrivs=1`, within five seconds, retaining its samples. The test preserves its initial snapshot and repeats the complete inspection before proceeding. A missing filter still fails the run. Regression tests reject the initial thread name, an unfiltered API, and `NoNewPrivs=0`.

The repeat [`jailer-d9gm_tlp`](../evidence/helper-memory/regression/jailer-d9gm_tlp.json) passed all six scenarios with the new default policy. Baseline and repeat exited zero; the disabled-seccomp control detected the negative policy. Descriptor exhaustion, VM OOM, and deadline enforcement retain exit `-9` and their expected classifications. They do not represent healthy guests. Helper-local counters remained `max=0`, `oom=0`, and `oom_kill=0`; hierarchical counters include the intentional VM OOM.

The readiness correction was added after the memory pair and validated in this original suite. Exact A/B copies of `jailer_checks.py` and `jailer_runtime.py` are retained in the evidence; cache/observer modules match the comparison.

## Repeat

Create two profiles from `config/jailer-profile.json`, adding `investigation_matrix: "host-load"`, `suite_timeout: 600`, `diagnostic_repetitions: 3`, and `diagnostic_pid_probe: true`. Set `helper_cache_policy: "retain"` in one and `"release-stopped-clones"` in the other. Run one campaign at a time:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_memory --host-load \
  --profile /path/to/retain-or-release-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results

python3 scripts/export_helper_memory.py \
  --runs /path/to/results/jailer-BEFORE /path/to/results/jailer-AFTER \
  --output evidence/helper-memory

python3 -m unittest discover -s tests -v
```

The exporter reviews protocol events and, after the campaigns, checks treated-clone hashes against those recorded before cache advice. This host-side integrity read is outside helper measurements. Raw observations and disks remain local; public copies contain reviewed fields and digests, excluding keys and absolute kernel-stack addresses.

References: [cgroup v2 accounting, events, and PSI](https://docs.kernel.org/admin-guide/cgroup-v2.html), [`POSIX_FADV_DONTNEED` and prior persistence](https://man7.org/linux/man-pages/man2/posix_fadvise.2.html).

Follow-up: [72 new shutdowns under load](shutdown-repetitions.en.md), using corrected cache handling and reported separately from this A/B.
