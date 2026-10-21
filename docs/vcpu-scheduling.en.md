# Where was the vCPU waiting?

[Português](vcpu-scheduling.md) · [Shutdown investigation](shutdown-investigation.en.md) · [Evidence](../evidence/scheduling/index.json)

The next stage adds [protocol probes and kernel stacks](vcpu-protocol.en.md): twenty clean shutdowns, ten captures spanning send/channel-return/join, and retained investigation errors. The historical panic still has not recurred.

We captured scheduling, signals, and KVM entry/exit events during shutdowns under host CPU load. The goal was to observe a spontaneous delay without stopping a vCPU with a debugger. **The historical panic did not recur in the final campaigns.** These events explain this sample's shutdowns; they do not establish the cause of the historical failure.

## Test setup

We used the **official Firecracker v1.17.0** binary with its one-second internal deadline and the lab's existing source-built kernel and disk. The five-second candidate did not replace it. Both campaigns check the four artifact hashes before and after execution.

Two CPU workers run in a separate unprivileged container on the same host CPUs **6 and 7** as the VM helper. Load is capped at two CPUs, 64 MiB, and eight tasks. The trusted helper remains capped at two CPUs, 768 MiB, and 256 tasks. It is privileged to prepare namespaces, cgroups, and tracing; the jailed VMM does not receive that privilege.

Each repetition alternates four arms: half a CPU or a two-CPU shutdown ceiling, with or without tracing. The order reverses on the next repetition. Every VM boots, confirms keyboard readiness, and runs two guest CPU loops for two seconds. We retain the historical loop-stop command without a `wait` acknowledgment. A second campaign also attempts to create 48 threads in a test process inside the VM cgroup, joins the threads it created, and checks isolation again. The 32-task ceiling must block the attempt.

VMs retain UID/GID 65534, zero effective capabilities, `NoNewPrivs`, seccomp, jailer PID/mount/network namespaces, a fresh disk copy per case, and memory/task/file-descriptor limits. Every case checks the policy across all observed threads. Clean shutdowns do not depend on removing isolation.

## Observe without freezing the thread

The [supervisor](../scripts/vcpu_scheduling.py) mounts tracefs inside the helper and creates a **dedicated instance** with a random name. It changes neither global instance options nor its buffer. During the guest loops, it discovers tracepoint IDs from `fc_vcpu 0` and `fc_vcpu 1`, requiring exactly one ID per name. Do not run other Firecracker microVMs during discovery: ambiguity fails the capture.

It then restricts shutdown events to those two numeric IDs:

- `sched_switch`, `sched_waking`, `sched_wakeup`, and `sched_process_exit`;
- `signal_generate` and `signal_deliver`;
- `kvm_entry` and `kvm_exit`.

It uses the `mono` clock, a 1 MiB buffer per CPU, and start/end markers. The window starts immediately before applying the shutdown quota and calling the API, and ends after observing VMM exit. A valid capture requires actual scheduler events, signal 35 generation/delivery, and **each** vCPU's exit, with no overruns or dropped events. Finally, the supervisor disables events and removes its own instance. It uses no `ptrace` and injects no pauses.

Runnable waiting time starts at `sched_wakeup` or a switch out in state `R` and ends at that thread's next switch in. Time sleeping in state `S` does not count. Intervals censored by the capture boundaries have no complete duration and are excluded from the maximum. This measurement combines CPU contention and any CPU restrictions; it cannot independently attribute each microsecond to either cause.

## Final results

The sequential campaigns `jailer-ivyhqnjk` and `jailer-phr137uc` completed **24/24 shutdowns with exit zero**: 12 validated captures and 12 untraced controls. The second included the task-limit test: all 12 attempts created 25 threads and encountered the ceiling. There was no panic, forced stop, or helper-local OOM; original artifacts were unchanged and cgroups were cleaned up.

Each cell contains three executions. Values are minimum / median / maximum total shutdown time in milliseconds:

| Campaign | Half CPU, control | Half CPU, trace | Two CPUs, control | Two CPUs, trace |
| --- | --- | --- | --- | --- |
| Guest loops | 81.8 / 119.8 / 160.0 | 87.6 / 106.3 / 107.6 | 104.1 / 234.4 / 295.1 | 102.3 / 102.5 / 324.4 |
| Loops + task ceiling | 164.8 / 179.0 / 230.0 | 99.5 / 139.2 / 229.3 | 83.0 / 171.7 / 194.0 | 127.3 / 151.7 / 188.7 |

Across the 12 final captures, the longest complete **runnable** waiting interval was **13.262 ms**. The longest interval from the last signal 35 generation to the next observed delivery was **3.842 ms**; to the thread-exit event, **3.942 ms**. Each capture contained exactly two signal generations, two deliveries, and two vCPU exits. All buffers recorded zero lost events.

Without the task-limit test, the VM throttling counter increased during none of the shutdowns. With that test, it increased once in `half-control-03` and once in `half-trace-03`: 16.301 ms and 9.297 ms of `throttled_usec`, respectively. Both shut down normally. The longest runnable wait, 13.262 ms, belongs to the latter capture; coexistence does not attribute the whole wait to the CPU quota.

Automated checks also passed: **18 tests**, including rejecting a marker-only capture and distinguishing sleeping from runnable waiting. The [concurrency control](../evidence/scheduling/lock-check.json) returned exit 2 when another campaign was attempted, before creating its containers.

The `cpu.stat` counter belongs to the VM cgroup, not every ancestor or the entire host. No increase there does not rule out external contention or ancestor throttling. Total shutdown time includes the API response and supervisor observation; it is not the internal `join` duration. This small sample, with different instrumentation between arms, does not establish that the two-CPU ceiling was consistently faster.

Valid traces show signal **35** generated by the VMM for the vCPUs, delivery, and thread exit. Generation and delivery are separate observations; the published chronology does not assume universally one-to-one matching. We observed no vCPU exceeding the one-second internal deadline in these campaigns. We did not collect complete kernel stacks or main-thread userspace channel operations either. An intermittent KVM or shutdown-protocol issue outside this sample remains possible.

## Attempts excluded from the comparison

- `jailer-twtd5stz`: all four VMs exited zero, but filters built from `/proc` and Docker IDs selected no target events. Only two markers appeared per capture. The original suite reported success; review marks this a **capture failure**. It does not demonstrate missing signals.
- `jailer-_k0n5jq0`: corrected discovery pilot; four clean exits and two valid captures. Excluded from the final campaign figures.
- `jailer-bhly3lu2`: twenty clean exits, but another campaign briefly overlapped its end. Load changed, so the entire campaign is excluded from the primary comparison.
- `jailer-atefhrbs`: stopped because of that conflict, exit 137, without a complete suite report. This is neither a spontaneous vCPU panic nor a passed run. The attempt and partial record hashes remain available.

The wrapper now holds a per-user lock during host-load campaigns. A second campaign from this wrapper is rejected before creating containers. This prevents overlap between these executions; it cannot exclude every other source of host CPU load.

## Repeat and review

With Docker/KVM and the lab's verified artifacts available, create an explicit profile outside the repository from `config/jailer-profile.json`, adding `suite_timeout: 600`, `investigation_matrix: "host-load"`, and `diagnostic_repetitions: 3`. Add `diagnostic_pid_probe: true` for the second campaign. Run **one campaign at a time**:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_scheduling --host-load \
  --profile /path/to/scheduling-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results

python3 scripts/export_scheduling_evidence.py \
  --runs /path/to/results/jailer-RUN \
  --output evidence/scheduling

python3 -m unittest discover -s tests -v
```

Local output retains consoles, complete traces, tracepoint formats, profiles, executed source, and test disks. GitHub receives reviewed event chronologies and summaries, raw file hashes, resource limits, and isolation checks. Memory addresses and unrelated task identities are omitted from the public copy; vCPU events, failures, and timings remain visible. No disks, keys, or memory dumps are published.

The next diagnosis needs to capture a spontaneous failure with these events, adding stacks or VMM protocol tracing if necessary. More successful shutdowns cannot replace that evidence.

References: [ftrace instances, clocks, and buffers](https://docs.kernel.org/trace/ftrace.html), [event filters](https://docs.kernel.org/trace/events.html), [Linux v6.18 signal event semantics](https://github.com/torvalds/linux/blob/v6.18/include/trace/events/signal.h).
