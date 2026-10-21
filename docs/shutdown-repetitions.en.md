# More shutdowns under load, with clone cache corrected

[Português](shutdown-repetitions.md) · [Memory investigation](helper-memory.en.md) · [Verifiable summary](../evidence/shutdown-repetitions/summary.json)

We expanded the sample after adopting cache release for stopped clones. Three sequential campaigns each repeat four scenarios six times: half a CPU and two CPUs during shutdown, with and without probes. These are 72 new VMs, separate from earlier results. The binary remains the official static musl Firecracker v1.17.0, with its original one-second internal wait. We did not inject vCPU pauses or adopt the five-second candidate.

## Method

Each VM boots, confirms keyboard readiness, runs two guest CPU loops and exercises its task ceiling. Two external workers compete with the helper on CPUs 6 and 7. Scenario order reverses on alternating repetitions. The wrapper lock keeps campaigns from overlapping.

The helper retains 768 MiB, no swap, two CPUs and 256 tasks. Each VM retains a 384 MiB cgroup ceiling, zero swap, 32 tasks and 64 descriptors; the guest has 256 MiB and two vCPUs. After boot and after the task probe, we check seccomp, `NoNewPrivs`, UID/GID 65534, zero execution capabilities, namespaces and the jailer root. These retain the previously checked properties; the bootstrap helper remains privileged and the [jailer guide's security limits](jailer.en.md) still apply.

Controls omit uprobes and stack sampling. The memory observer remains active in every scenario, targeting 100 ms samples plus phase markers. Instrumented cases probe send, response wait, vCPU return and `join`; stack reads are sequential and can fail when a thread exits. Instrumentation consumes CPU and affects observed durations.

## Results

**72/72 shutdowns exited zero**, with no panic, timeout or forced cleanup. All 36 instrumented captures passed protocol and scheduler review. All 72 isolation and task-ceiling checks passed, the four original artifacts remained unchanged and all 144 treated clone files were hash-verified after the campaigns.

| Scenario | Samples | Shutdown min / median / max (ms) |
| --- | ---: | ---: |
| Half CPU, control | 18 | 93.210 / 140.575 / 280.723 |
| Half CPU, instrumented | 18 | 125.477 / 195.200 / 330.106 |
| Two CPUs, control | 18 | 75.894 / 146.622 / 234.263 |
| Two CPUs, instrumented | 18 | 93.396 / 193.056 / 303.290 |

| Campaign | Helper peak (MiB) | Limit events / OOM / OOM kill | PSI some / full (ms) | Direct reclaim (pages) |
| --- | ---: | ---: | ---: | ---: |
| `jailer-tuksn0jb` | 326.75 | 0 / 0 / 0 | 30.380 / 30.380 | 0 |
| `jailer-mbk7ng3x` | 324.28 | 0 / 0 / 0 | 0.000 / 0.000 | 0 |
| `jailer-1ky6mv3w` | 322.23 | 0 / 0 / 0 | 1.398 / 0.590 | 0 |

Peaks exceeded the previous corrected campaign’s 262.45 MiB, while remaining below the 768 MiB ceiling. PSI increments were not all zero: we retain 30.380 ms for both `some` and `full` in the first campaign, and 1.398 / 0.590 ms in the third. We do not assign a specific cause to these small waits. No direct reclaim was recorded.

The longest probed response wait was **8.128 ms**; the longest `pthread_join` interval was **91 µs**. Stack reads included 3,595 nonempty samples, 265 empty samples and 23 `FileNotFoundError` records while accessing a task already absent. Empty and error counts may overlap. Seven nonempty VMM stacks fell entirely within a response-wait interval; none fell entirely within `join`. This does not establish absence of activity during that short stage.

All **31 automated tests** passed. The first execution of the three new tests found `KeyError` in the aggregator with synthetic records omitting optional fields. We corrected optional-field normalization and repeated the suite; VM execution code did not change. The [closing check](../evidence/shutdown-repetitions/closing-check.json) records that development error, final validation and absence of containers, trace instances or probes left by these campaigns.

## What this establishes

This campaign expands the evidence for shutdown under CPU contention with corrected cache handling. Not reproducing the historical panic neither explains its cause nor proves it eliminated. Total shutdown duration includes other stages and should not be directly compared with the channel's internal one-second deadline.

The policy persists and advises cache release for the two clone files only after confirming the VM stopped. Files remain available. Peaks may vary between campaigns: shared cache, first touch and other host activity affect accounting. `DONTNEED` is advisory and does not guarantee full eviction.

Memory observation ends after scenarios and input verification, before the observer serializes its own file. Export and host-side integrity reads happen after all three campaigns, outside that window. Memory fields and stack reads are not atomic snapshots. These samples are not independent production trials; we infer no reliability guarantee from an absence of failures.

## Evidence and reproduction

The [index](../evidence/shutdown-repetitions/index.json) links memory reports and hashes. The `protocol/` directory retains stages, timing, symbolized stacks, empty reads and errors; the [isolation report](../evidence/shutdown-repetitions/isolation-and-provenance.json) contains snapshots and executed-source comparisons. Full profiles appear in protocol reports. Disks, keys and absolute stack addresses are excluded from publication. Earlier evidence, including failures, remains intact.

Copy `config/jailer-profile.json` into a local profile and add:

```json
{
  "investigation_matrix": "host-load",
  "diagnostic_repetitions": 6,
  "diagnostic_pid_probe": true,
  "suite_timeout": 600
}
```

These fields supplement the profile; retain hashes, limits and `helper_cache_policy: "release-stopped-clones"`. Run the following three times, waiting for each campaign to finish and retaining every directory, including failures:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_memory --host-load \
  --profile /path/to/repetition-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results
```

After the campaigns, export the three complete directories:

```bash
python3 scripts/export_helper_memory.py \
  --runs /path/to/results/jailer-ONE /path/to/results/jailer-TWO /path/to/results/jailer-THREE \
  --output evidence/shutdown-repetitions
python3 scripts/export_shutdown_repetitions.py --evidence evidence/shutdown-repetitions
python3 -m unittest discover -s tests -v
```

The memory exporter also serves the earlier A/B; its generic method text mentions retaining versus releasing cache. Here all three profiles use the corrected policy, with no new `retain` arm. The protocol exporter's historical `primary` flag targets five-repeat campaigns; this sample is selected in `campaigns.json` and the 72-case summary. If an executor is interrupted without a final report, preserve its files and use partial protocol export; do not label an incomplete campaign as passed.
