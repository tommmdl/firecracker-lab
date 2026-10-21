# Where do the small memory stalls come from?

[Português](memory-stalls.md) · [Earlier repetitions](shutdown-repetitions.en.md) · [Evidence](../evidence/memory-stalls/attribution-index.json)

Two earlier campaigns recorded small memory PSI increments despite corrected cache handling and no helper limit events. We reviewed every increment window and added I/O observations, host counters and kernel stacks at `psi_memstall` entry and exit. The aim was to observe the involved paths before proposing another behavior change.

## Added observations

The `vcpu_pressure` runner retains actual boot, keyboard readiness, guest CPU loops, the task-ceiling probe and shutdown from the existing matrix. It adds memory/I/O/CPU PSI, `cpu.stat`, `io.stat`, page faults, refaults and page-reclaim counters for the helper, supervisor and VM-parent cgroups. It also reads global host counters, including allocation and compaction. These include unrelated workloads and do not exclusively measure this lab.

An owned tracefs instance records `psi_memstall_enter` and `psi_memstall_leave` with stacks. A unique marker identifies the helper PID in the tracer's namespace; `set_event_pid` and `event-fork` restrict records to that helper and future descendants. Probes are registered in the shared kernel and incur instrumentation cost, even though records are filtered. Cleanup removes only the instance and two probes created by this run. There is no `ptrace`, injected pause, global `drop_caches` change, key access or modification of original inputs.

Limits remain unchanged: helper 768 MiB, no swap, two CPUs and 256 tasks; each VM 384 MiB in its cgroup, zero swap, 32 tasks and 64 descriptors. Guests retain two vCPUs and 256 MiB. Two unprivileged workers compete on CPUs 6 and 7. Official Firecracker v1.17.0 retains its original one-second internal deadline. UID/GID, seccomp, `NoNewPrivs`, execution capabilities, namespaces and jailer root are checked after boot and after the task probe. Existing [security limitations](jailer.en.md) still apply.

## Findings

In the larger earlier increment, 29.593 of 30.380 ms of `some` PSI fell in startup and first-VM preparation windows. The other nonzero campaign had later increments. Historical evidence lacks `psi_memstall` stacks and I/O PSI; we do not retrospectively assign a cause to each stall.

| Run | New memory probes | Completed / planned VMs | Campaign status | Memory PSI some / full (ms) | Peak (MiB) |
| --- | --- | ---: | --- | ---: | ---: |
| Pilot: `jailer-qcvjl_tt` | yes | 4 / 4 | passed | 189.970 / 167.363 | 255.05 |
| Enabled: `jailer-1zzwgi68` | yes | 12 / 12 | passed | 0.000 / 0.000 | 285.68 |
| Disabled, before PID fix: `jailer-c1sxrbwi` | no | 10 / 12 | **failed** | 127.092 / 127.092 | 269.82 |
| Enabled again: `jailer-e5829fe6` | yes | 12 / 12 | passed | 58.791 / 58.104 | 288.74 |
| Disabled, after PID fix: `jailer-0d2gc21s` | no | 12 / 12 | passed | 2.871 / 2.871 | 286.46 |

All **50 completed shutdowns were clean**, with no panic or timeout; this does not turn the interrupted campaign into a success. One startup failed and the final scenario was unattempted. All 25 protocol/scheduler captures passed review for completed cases. The four original artifacts remained unchanged and all **100 treated clone files** retained their hashes. Both files from the clone that never reached boot also matched inputs.

All helpers in these five runs retained `max=0`, `oom=0` and `oom_kill=0`, with zero observed swap. The highest peak was 288.74 MiB, below the 768 MiB ceiling. This does not establish absence of host pressure.

Pilot stacks showed readahead/refault paths and **90 allocation-slowpath entries within the memory window**, all on the helper’s main thread. The allocation stack passes through `do_sendfile`, ext4 writes and folio allocation, consistent with clone copying. Most pilot `some` PSI, 175.935 ms, fell within first-clone preparation. This reproduction recorded a larger stall than the earlier small increments and remains in the table.

The third campaign had **three slowpath entries inside the window**, also on the main thread; eight more occurred afterward, during report finalization. The full trace also contains 602 readahead entries. The cgroup recorded 3,221 file refaults and 2,496 pages scanned by `kswapd`, with `pgscan_direct=0`. During the same period, host counters increased by 277 pages for `pgscan_direct`, two `allocstall_movable` occurrences, three `compact_stall` occurrences and one swap-in page. These global counters include unrelated workloads; we do not assign that swap to the guest, whose swap ceilings remain zero.

One instrumented campaign had zero memory PSI and a valid capture with no `psi_memstall` events, but **450.166 ms of `some` I/O PSI**. We therefore do not convert every I/O wait into a memory stall. The repeat without new probes ended with 2.871 ms of memory PSI. The sequence does not prove enabling probes raises or lowers PSI: host and cache conditions changed between runs.

All three new captures (pilot and two probe-enabled campaigns) had zero buffer loss and confirmed cleanup. The valid empty capture is retained too. `io.stat` exists at the helper root but was unavailable in the supervisor and VM-parent cgroups because the I/O controller was not delegated to children. Reports preserve `FileNotFoundError` records and counts rather than converting missing values into zero bytes. I/O PSI remained available for these children.

### A real startup failure

Campaign `jailer-c1sxrbwi` stopped after ten VMs with `ValueError: invalid literal for int() with base 10: ''`. Attempt `boost-control-03` left API-startup console output and a PID file that later contained `343`. The previous reader called `int(pidfile.read_text())` as soon as the file existed. A creation/publication race is inferred from this error and read site; the empty file was not captured at that instant. No boot was validated for that scenario. Cgroup cleanup was confirmed, and the final arm never started.

The reader now waits for an absent or empty file within the unchanged ten-second deadline. Malformed contents still fail; PID, socket and all subsequent inspections remain mandatory. Two tests cover empty creation followed by publication and malformed-content rejection. In the actual repeat, eight of twelve VMs encountered pending reads (absent **or** empty), totaling eleven attempts; the counter does not distinguish these states. All twelve completed boot, isolation checks and shutdown.

All **39 automated tests** and the [six original controls](../evidence/memory-stalls/regression/jailer-quppavgn.json) passed after the correction. VM OOM, descriptor exhaustion, deadline enforcement and disabled seccomp remain negative controls with their expected classifications, not healthy guests. The three initial campaigns executed identical source; only the PID reader and its call changed before the repeat. Earlier pilot and reader sources are preserved with hashes. The [closing check](../evidence/memory-stalls/closing-check.json) records isolation, provenance and absence of containers, trace instances or probes left by this experiment.

## Interpretation

Memory PSI is not the `memory.max` event count, an OOM count or the duration of one call. The [PSI documentation](https://docs.kernel.org/accounting/psi.html) describes microsecond totals for stall states, with `some` and `full` views. [Linux 6.18 readahead](https://github.com/torvalds/linux/blob/v6.18/mm/readahead.c) can enter `psi_memstall` for workingset recovery, so an increment does not establish container-limit exhaustion. The [page allocator](https://github.com/torvalds/linux/blob/v6.18/mm/page_alloc.c) provides another path into that state. These upstream sources help interpret stacks; they do not replace exact reproduction of this host's WSL 6.18.54.1 kernel.

Stacks identify paths reaching the instrumented point in this reproduction. A function call does not necessarily mean positive accounted stall time; nested calls and the tracer itself also affect observation. We do not sum entry/exit differences to invent a PSI total. Event overlap with increment windows is temporal correlation, subject to sequential reads and accounting delay.

There are two test dimensions: half the VMs retain earlier protocol probes and procfs stack sampling, and half omit them. Separately, the new memory probes are enabled or disabled for an entire campaign. All scenarios observe cgroups. Campaigns are sequential without global cache resets; they do not isolate a causal tracer-overhead or cache-warming effect.

Memory observation starts after Python imports and ends before serializing its own file. The trace has a slightly wider window, including that later serialization. Reports retain all events and distinguish those overlapping increment windows. Child-cgroup counters disappear after removal; absence is not converted to zero. `memory.stat` and PSI can update at different times. Disks and full raw reports remain local, with reviewed fields and hashes published; public stacks retain symbols and omit absolute addresses.

## Changes and remaining work

We retain clone-cache release only after VM exit, with persistence and hashes. This investigation adds optional diagnostics; it does not raise limits, disable security or adopt an optimization from one run. Assigning a cause to every earlier small stall would require stacks captured during that execution. Testing a specific allocation or reading mitigation still needs a reproducible initial condition and a narrowly defined intervention. The historical vCPU panic remains unexplained.

## Reproduction

Copy `config/jailer-profile.json` into a local profile. Retain hashes, limits and `helper_cache_policy: "release-stopped-clones"`. Add:

```json
{
  "investigation_matrix": "host-load",
  "diagnostic_repetitions": 3,
  "diagnostic_pid_probe": true,
  "suite_timeout": 600,
  "memory_stall_trace": true
}
```

Run campaigns sequentially with `memory_stall_trace` set to `true`, `false` and `true`, retaining all directories and failures. This field changes only the new memory probes; the protocol matrix stays the same.

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_pressure --host-load \
  --profile /path/to/pressure-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results
```

After campaigns finish, export the pilot and three campaign directories, plus historical directories if available:

```bash
python3 scripts/export_helper_memory.py \
  --runs /path/to/PILOT /path/to/ON /path/to/OFF /path/to/ON-AGAIN \
  --output evidence/memory-stalls
python3 scripts/export_pressure_evidence.py \
  --runs /path/to/PILOT /path/to/ON /path/to/OFF /path/to/ON-AGAIN \
  --historical /path/to/HISTORICAL-ONE /path/to/HISTORICAL-TWO /path/to/HISTORICAL-THREE \
  --output evidence/memory-stalls
python3 -m unittest discover -s tests -v
```

Complete exporters require a final report. After an interruption, preserve files and use partial protocol export; missing or lossy capture does not establish absence of stalls. The memory exporter's generic index still describes the earlier A/B; all campaigns here use corrected cache handling. The pilot preceded host-counter collection and additional trace-option readbacks and is identified separately.
