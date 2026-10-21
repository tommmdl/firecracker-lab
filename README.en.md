# Firecracker in practice

[Português](README.md) · English

This lab started with a microVM that booted successfully while its controller reported missing guest evidence. We now have a repeatable test: copy the disk, start the VM, read responses through the console and stop it through the API, checking the original files afterwards.

The next stage adds a recipe to compile Linux 6.12.113 and BusyBox 1.38.0 from pinned sources and create a fresh disk. [Build the kernel and image from scratch](docs/build-from-source.en.md). The focus remains local boot on Ubuntu WSL2 with KVM; networking, SSH and workloads belong to later exercises.

## What the test checks

- The VM uses a disk copy whose SHA256 matches the original before boot.
- The guest shell responds with its UID, kernel and `/dev/vda` mounted as writable ext4.
- Markers belong to the current run; an echoed command does not count as a response.
- The API accepts `SendCtrlAltDel` with HTTP 204, and Firecracker exits with code zero without a forced stop.
- The original disk, kernel, binary and configuration retain their hashes.

The reader normalizes CR/LF and removes ANSI colors, keeping the raw console in the local run directory. The script waits for the root prompt provided by this image's automatic login; it does not send another `root` or attempt authentication.

## Run it again

You need Linux x86-64, Python 3, read/write access to `/dev/kvm`, and an existing compatible Firecracker binary, kernel and rootfs. The guest needs automatic root login on the console, `id`, `uname`, and either `findmnt` or the procfs/sysfs queries used by the minimal image. The source disk must be offline, with no other VM or mount using it. The script's lock is advisory and only coordinates tools that respect that lock.

The existing environment stores its artifacts under `$HOME/fc-lab`. Adjust these paths to your environment:

```bash
python3 -m unittest discover -s tests -v

python3 scripts/boot_check.py \
  --firecracker "$HOME/fc-lab/firecracker" \
  --kernel "$HOME/fc-lab/vmlinux" \
  --rootfs "$HOME/fc-lab/rootfs.ext4" \
  --config "$HOME/fc-lab/vm-config.json" \
  --output-parent "$HOME/fc-lab"
```

The example under `config/` matches the new minimal image; the original Ubuntu configuration remains preserved in the local directory.

Each run creates a unique `boot-*` directory containing `rootfs.ext4`, `vm-config.json`, `console.log` and `result.json`. Use a short path on the Linux filesystem for the Unix socket. Allow at least the disk size per run; copies remain available for inspection and cleanup is manual, with the VM stopped.

The default deadline for guest responses is 90 seconds, after copying and checking the disk. Shutdown gets another 30 seconds. Exit code `0` means the test passed; `1` means a failure recorded in `result.json`; `2` means a preparation/argument error. Preparation can fail before a report exists. The script does not retry failed tests.

To exercise a real deadline failure, repeat the command with `--boot-timeout 3`. This caused a failure with the first stage's Ubuntu guest but does not guarantee failure with the new minimal image or on another machine. The outcome must remain failed even if the VM shuts down correctly afterwards. [Procedure and results](docs/validation.en.md).

## Evidence and next steps

- [Jailer: isolation, resources and real failures](docs/jailer.en.md)
- [Source build and verification](docs/build-from-source.en.md)
- [Boot validation, repeat run and failure control](docs/validation.en.md)
- [Reviewed results](evidence/boot-results.json)
- [Preserved earlier failure](evidence/historical-failure.json)
- [Next exercises](docs/roadmap.en.md)
- [Example configuration](config/vm-config.example.json)

The first stage used the existing Ubuntu artifacts; their hashes identify the files without authenticating their origin. The new recipe verifies Linux and BusyBox sources and builds a separate minimal image. The repository does not distribute kernels, disks, binaries or private keys. There is no GitHub Actions boot test at this stage.

The `jailer` now has local isolation and resource exercises in its [own guide](docs/jailer.en.md). Evaluation of isolation for untrusted code remains incomplete. Implementation and tests had assistance from the assistant; the evidence records what ran in the local environment. The code and reviewed evidence in this lab are public.

## What we have exercised with the jailer

The [final profile](config/jailer-profile.json) passed two runs of six scenarios. Tests observed UID/GID 65534, cleared execution capabilities, separate roots/namespaces and active seccomp on threads after boot. Real load exercised CPU, memory, tasks, descriptors and deadlines. Disabled-seccomp, OOM and deadline controls are not approved profiles or healthy guests.

The historical shutdown panic remains preserved. The [investigation](docs/shutdown-investigation.en.md) located SIGSEGV in musl’s abort path in a control, corrected keyboard readiness and tracer collection, and compared two static musl binaries under host load. After those corrections, each variant completed ten clean shutdowns. The five-second candidate tolerated a 1.5-second vCPU hold and still failed the long control; it remains experimental, with the historical delay’s cause unresolved. [Earlier results](evidence/jailer-results.json) · [Retained failures](evidence/shutdown-investigation.json).

The [scheduler and signal investigation](docs/vcpu-scheduling.en.md) added 24 clean shutdowns under host load, including 12 valid traces, untraced controls, and a task-limit test. The longest observed runnable wait was 13.262 ms; the historical panic did not recur and its cause remains unresolved. Invalid and interrupted attempts are also published.

The [protocol and kernel-stack investigation](docs/vcpu-protocol.en.md) traces sending, channel waiting, and join in the official binary. The final campaign completed 20 clean shutdowns, with ten instrumented captures and actual futex/KVM stacks. Channel waiting reached 5.867 ms and join reached 83 µs; the historical panic remains unexplained. Setup, validator, and read failures are also published.

The [helper memory investigation](docs/helper-memory.en.md) identified accumulated clone cache and adopted cache release after each VM stops. Under host load, the A/B peak fell from 768 to 262.45 MiB and limit events went from 7,434 to zero, retaining the same ceilings. The pair completed 24 clean shutdowns, preserved file hashes, and passed 28 automated tests. Regression evidence also retains an API readiness failure and its correction.

## Hardening still to do

The [real test records](evidence/source-build-results.json) include a retained shutdown failure, two passing boots after the correction and ten regression tests. They also record rejection of a modified source archive. These ran locally, without a GitHub Actions boot run.

Before running third-party workloads, the remaining work includes:

- Reduce bootstrap privileges used in the `jailer` exercises, review `CapBnd` and seccomp policy, and extend validation to a dedicated environment. The current helper is privileged; UTS, IPC, user and cgroup namespaces remain shared with it.
- Review the minimal kernel's CPU mitigations. Its console reported `Spectre V2 : User space: Vulnerable` and `Speculative Store Bypass: Vulnerable`; findings remain visible.
- Review the direct root shell before adding networking. TAP, key-based SSH and outbound rules with allow/deny tests have not been validated.
- Package the host toolchain and strengthen BusyBox origin verification. The kernel currently has a verified OpenPGP signature; BusyBox has a pinned SHA256 from official HTTPS.

The exercise demonstrates boot and shutdown in the recorded environment. Complete isolation for untrusted code remains unproven. [Build and limits](docs/build-from-source.en.md) · [Next exercises](docs/roadmap.en.md).

Reference: [official Firecracker v1.17.0 guide](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md). No license has been defined for this repository's own content; external components have separate licenses.
