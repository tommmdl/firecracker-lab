# Why did shutdown end with SIGSEGV?

[Português](shutdown-investigation.md) · [README](../README.en.md) · [Reviewed results](../evidence/shutdown-investigation.json)

The original run booted successfully, remounted the disk read-only and panicked while waiting for vCPU 0 to exit. The process then ended with SIGSEGV. We investigated these as separate stages. The SIGSEGV sequence was reproduced with instrumentation; the exact reason for the delay **in the historical run** remains unresolved.

## What the source and tests showed

During teardown, Firecracker v1.17.0 sends `Finish` to its vCPUs, requests an exit from KVM and waits for each thread's response channel to disconnect. Its internal timeout is **one second**, separate from the supervisor's external 15-second deadline. A thread that does not finish in that interval triggers a VMM panic.

The panic does not identify why the thread was late. Scheduling delays, a kernel path or an unfinished protocol step are possibilities. The historical console has no stack, per-thread state or signal capture that can distinguish them.

In the controlled test, the supervisor used `ptrace` to hold a single vCPU at the teardown message while allowing the main thread to run. This triggered the same panic. Tracing captured two SIGABRT attempts, followed by SIGSEGV at the `hlt` instruction inside musl's `a_crash`. The official binary's relative address `0x26cae6` was checked against its debug symbols and executable disassembly.

Firecracker runs as **PID 1** in the namespace created by the jailer. That PID has special signal semantics. In the observed path, the abort attempts did not terminate the process and musl reached its emergency instruction. Thus this control's SIGSEGV follows the panic; it is not evidence of an accidental guest pointer access. The old run was not traced: assigning this exact sequence to it remains an inference consistent with its logs, version and profile.

We did not remove the PID namespace to change the exit status or disable seccomp to obtain a passing test.

## CPU quota: a tested hypothesis, without an easy conclusion

The matrices retain every run, including results that weaken the initial hypothesis:

- Repeated shutdowns under half a CPU, following two guest CPU loops, completed normally. These controls kept the historical loop-stop command without a `wait` acknowledgment. They do not repeat the entire earlier combination with the task-limit probe.
- One control reduced the ceiling to only 1 ms of CPU per second at teardown. Observed shutdown took about **10.28 s**, with substantial throttling, but exited with code **0**.
- In its paired artificial-pressure control, restoring the bounded two-CPU ceiling after roughly 250 ms allowed completion in about **0.63 s**, also with code **0**.

CPU starvation can delay shutdown, but these results **do not establish that it caused the historical panic**. The one-second timer starts at the channel wait; a ten-second total delay does not mean that particular wait exceeded one second. Tracing also pauses execution and changes scheduling. These timings support a local comparison, not a benchmark.

## Candidate correction: tolerate a short delay, keep a deadline

We built two variants from the same Firecracker commit with the same toolchain and `Cargo.lock`: stock and a candidate whose only source change increases `VCPU_JOIN_TIMEOUT` from one to five seconds. The [builder](../scripts/build_shutdown_candidate.py) pins the commit, source archive checksum and base build image digest. The Dockerfile and derived image ID are recorded too. Resolved APT package versions appear in the log; APT repositories are not a pinned snapshot, so future bit-for-bit reproduction is not promised. Source origin is GitHub over HTTPS; the pinned checksum does not add an independent maintainer signature.

The A/B stock variant is rebuilt, not the official distribution file used in the initial analysis. Both A/B variants share the same seccomp policy source and generated BPF filter, with recorded hashes. Falling back to an empty filter fails the build.

The candidate is a local experiment. It does not silently replace the official binary and is not an upstream fix. A longer timeout reduces sensitivity to transient delays; it does not repair a deadlock, lost event or KVM issue. The long-hold control checks that a thread still prevented from finishing continues to fail.

Explicit profiles and paired results are retained as evidence. UID/GID, zero effective/permitted/inheritable/ambient capabilities, `NoNewPrivs`, seccomp and jailer namespaces are checked after each scenario boots. Memory, task and descriptor limits remain active. CPU ceilings stay finite. Induced process failures remain failures even when an experiment confirms expected behavior.

## Validate the executable before starting a VM

An earlier compilation succeeded, but both executables contained `PT_INTERP` pointing to `/lib/ld-musl-x86_64.so.1`. There was no `DT_NEEDED`; the interpreter still made them unsuitable for the minimal jail. This stage failed artifact validation before any runtime test of that candidate.

The `musl-gcc` driver used for final linking added the interpreter. The builder now uses `cc` for that step, retaining the `x86_64-unknown-linux-musl` target and musl compiler for C dependencies. It first builds and executes a minimal static program, then applies the same checks to stock Firecracker and the candidate. It requires no `PT_INTERP` or `DT_NEEDED`, successful `--version` execution and an artifact hash. The wrapper repeats static and hash validation before starting the load generator or VM helper.

The negative control used the rejected executable and returned code 2 without starting test containers. We also retained an incorrectly typed local image reference that returned code 125. The builder reads the complete ID from Docker's generated file and checks resolution through `docker image inspect`; IDs are not manually expanded.

## Two automation failures found under load

In the first campaign with static binaries, stock repetition seven received HTTP 204 but did not start rebooting. Keyboard registration appeared after the request; the VMM remained in its event loop and the vCPUs in KVM. The quota accumulated no additional throttled periods during those 15 seconds. The supervisor terminated the test copy with SIGKILL. This happened **before teardown**, without the join panic, and increasing the internal timeout from one to five seconds does not fix it.

The same readiness race appeared in the candidate's short-hold control. An available shell does not prove a keyboard handler is active. The correction queries `/proc/bus/input/devices` inside the guest and waits for the `AT Raw Set 2 keyboard` device with a `kbd` handler. A nonce-delimited response prevents accepting the echoed command. Only then is shutdown requested; an old lost request is not automatically retried into a passing result.

Stock controls also captured the panic and SIGSEGV, but the tracer did not confirm process termination. Those results remain failed, with an unknown final status when collection was incomplete. The revision releases traced vCPUs before detaching the main thread, reaps their wait statuses and lets `hlt` fault again after detaching. The observed signal and collected final exit status are separate observations.

The earlier campaign remains intact in the evidence. The new comparison uses the same binaries and limits, with revised readiness and collection for both variants.

## Repetitions under host load

The load generator runs in another container without privileges, capabilities or networking, as UID/GID 65534. Two processes contend on the same WSL/KVM host CPUs as the VM helper: CPUs 6 and 7 in this eight-CPU environment. The generator has a two-CPU ceiling, 64 MiB and eight tasks, checks its workers remain alive and is removed afterward. Its maximum lifetime is 600 seconds.

This applies CPU pressure to two selected WSL CPUs; it does not stress memory, I/O or the Windows scheduler. Reports retain affinity, limits, generator health and CPU pressure. Non-injected repetitions alternate an idle guest and two guest CPU loops before shutdown. Loaded cases also repeat the trusted same-cgroup thread-creation probe, followed by another runtime protection check; held-thread controls are reported separately.

| Rebuilt variant, corrected collection | Clean non-injected shutdowns | Unexpected failures | Minimum / median / maximum time |
| --- | --- | --- | --- |
| Stock, 1 s wait | 10/10 | 0 | 0.107 / 0.197 / 0.376 s |
| Candidate, 5 s wait | 10/10 | 0 | 0.110 / 0.197 / 0.256 s |

| Control under the same host load | Stock 1 s | Candidate 5 s |
| --- | --- | --- |
| Planned 1.5 s hold of one vCPU | Panic after about 1.06 s; collected termination: SIGSEGV (`-11`) | Thread released; clean shutdown (`0`) |
| Planned 6 s hold of one vCPU | Panic after about 1.06 s; SIGSEGV (`-11`) | Panic after about 5.01 s; SIGSEGV (`-11`) |

Panic timings start at the injected hold, not at boot. Failed VMs terminate before completing the planned hold. The matrix confirms expected behavior, but **the three panicking VMs remain process failures**, not clean shutdowns.

[Stock, corrected campaign](../evidence/shutdown-runs/jailer-koveu1da.json) · [Candidate, corrected campaign](../evidence/shutdown-runs/jailer-qc_wk0g4.json).

Before the automation correction, [stock had 9/10 clean shutdowns and two inconclusive controls](../evidence/shutdown-runs/jailer-w3_psdae.json); [the candidate had 10/10 clean shutdowns but its short control failed before teardown](../evidence/shutdown-runs/jailer-z3dqb0mq.json). Both earlier campaigns remain failed. Their counts are kept separate from the corrected comparison.

All 24 VMs in the corrected comparison had confirmed keyboard readiness and runtime approval under the observed jailer policy. Load generators were alive at the beginning and end, without OOM; final snapshots showed about 200% CPU, measured after each matrix. Original input hashes stayed unchanged and cgroups were removed. The 15 regression tests passed too.

The candidate tolerated the controlled short delay and retained a deadline for the long delay. **The historical spontaneous delay's cause and elimination remain unproven.** Ten repeats per variant on one host do not rule out a rare failure. The candidate remains experimental; the default profile uses the official binary.


## Repeat locally

Use the known artifacts from the [kernel/rootfs build](build-from-source.en.md), Docker/KVM and the reviewed Firecracker/jailer v1.17.0 pair. The execution helper remains **privileged and trusted**, without networking, capped at two CPUs, 768 MiB and 256 tasks. It is not a secure boundary for untrusted operators.

```bash
python3 scripts/run_jailer_lab.py --suite shutdown_investigation \
  --binary-dir "$HOME/fc-lab" \
  --artifacts "$HOME/fc-build/run-01/artifacts" \
  --output-parent "$HOME/fc-lab"

python3 scripts/build_shutdown_candidate.py \
  --output "$HOME/fc-shutdown-build-new" \
  --jailer "$HOME/fc-lab/jailer"
```

The build output must be a new directory. Building uses an ordinary unprivileged container with networking for lockfile-pinned dependencies, a two-CPU ceiling, 3 GiB and 256 tasks. Binaries and explicit profiles are placed in `stock/` and `candidate/`, with hashes in the report. Generated profiles select ten repeats and both controls, with an external deadline of up to 600 seconds for the entire matrix.

To test a variant under host CPU load:

```bash
python3 scripts/run_jailer_lab.py --suite shutdown_investigation --host-load \
  --profile "$HOME/fc-shutdown-build-new/candidate/profile.json" \
  --binary-dir "$HOME/fc-shutdown-build-new/candidate" \
  --artifacts "$HOME/fc-build/run-01/artifacts" \
  --output-parent "$HOME/fc-lab"
```

Repeat with `stock` instead of `candidate` for the original control. The builder's optional `--reuse-from` can reuse a known earlier build directory, recorded in provenance; the clean recipe does not require it. Do not replace the regular profile with a diagnostic profile.

The tracer is specific to Linux x86_64. The wrapper retains snapshots of scripts/profile, launch commands, per-scenario reports, consoles and hashes. Every guest uses a fresh disk copy. Inputs are mounted read-only and their before/after hashes are compared; original disks are not migrated or modified.

## Investigation failures

The first tracer kept delivering SIGSEGV under `ptrace`, repeatedly stopping at the same instruction until the external deadline. Cleanup killed that VM with SIGKILL. This experiment remains **failed**, separately retained; it is not a natural shutdown and does not replace the old SIGSEGV result. The next version records the first fault, detaches and confirms actual SIGSEGV termination.

The first build was also stopped: the image provided Cargo but lacked the Git executable requested by the project configuration. The script now uses Cargo's internal Git transport. Exit 137 from that administrative stop is not reported as OOM or a passing test. The next attempt lacked libclang. After installing it, the musl-hosted compiler still could not load the dynamic library used by bindgen. The final builder uses a GNU host and static musl target. Linux headers also needed to be exposed to musl-gcc, followed by the libseccomp development library for filter generation. Header layout was aligned with the upstream development container. The missing rustfmt warning was non-fatal and was resolved too. All these build attempts remain failed in the records.

Capturing the original spontaneous delay, comparing another host/kernel and reviewing the candidate before any adoption remain open. These results do not certify isolation for arbitrary untrusted code. Other CPU, networking, privileged bootstrap and `CapBnd` limitations remain in the [jailer guide](jailer.en.md).

## Analysis sources

- [vCPU wait, events and kick in v1.17.0](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/vstate/vcpu.rs)
- [Finish events during teardown](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/vstate/vm.rs)
- [Panic hook](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/firecracker/src/main.rs) and [signal handlers](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/signal_handler.rs)
- [Upstream build environment](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/tools/devctr/Dockerfile)
- [musl abort](https://git.musl-libc.org/cgit/musl/tree/src/exit/abort.c) and [PID 1 rules](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
