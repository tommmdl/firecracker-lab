# How we validated boot

[Português](validation.md) · [Back to README](../README.en.md)

## The earlier problem

On 2026-10-16, Firecracker exited with code zero and its console showed root UID, kernel `6.18.54+` and writable ext4 on `/dev/vda`. Nevertheless, the controller reported `Guest evidence incomplete`.

The preserved console contains both markers, with `CR CR LF` line endings. A literal search for `\nAUDIT_GUEST_END\n` does not match those raw bytes. Normalization resolves that difference. An extra `root` was also sent after automatic login, resulting in `-bash: root: command not found`.

The old controller's code was not retained in the repository. We do not claim to have rerun that controller: the regression uses a reviewed, real console excerpt to reproduce the line framing mismatch. The [old result](../evidence/historical-failure.json) remains recorded as an automation failure, despite manual evidence of boot.

## The new controller

The [script](../scripts/boot_check.py) uses only Python's standard library. It creates a unique directory, hashes the four original artifacts, copies the disk and verifies the copy before starting a single VM. The generated configuration points to the copy; the original is not edited.

A pseudo-terminal receives console bytes. Interpretation normalizes CR/LF and removes ANSI sequences; the raw log remains intact. The controller waits for the root prompt and sends a query with a random identifier for each run. It only accepts complete markers on their own lines and valid responses between them. The echoed command cannot pass that check.

The guest must respond with UID `0`, a kernel value and `/dev/vda ext4` mounted with the `rw` option. The controller then sends `SendCtrlAltDel` through the Unix socket API, requiring HTTP 204 and exit code zero. If shutdown exceeds its deadline, it stops only the process it created and fails the test. Preserving all four hashes is also mandatory.

## Real runs in this stage

The [reviewed results](../evidence/boot-results.json) record two passing runs and the reduced-deadline control, along with hashes and warnings extracted from the consoles. Time to guest response uses the controller's monotonic clock, starting after the copy and initial hashes; it is not a kernel startup benchmark. Total time includes preparation and final checks.

Both normal runs started from fresh copies of the same original disk. Tests use Firecracker v1.17.0, 2 vCPUs, 1024 MiB and no configured network interface. The guest is Ubuntu 24.04.5 LTS with kernel `6.18.54+`.

The control uses the same README command with `--boot-timeout 3`. We expect exit code `1` and no complete guest evidence before the deadline. Successful shutdown after a timeout does not turn the result into a pass. This deadline was chosen for this environment; a faster machine may require a different value to reproduce failure.


| Run | Controller outcome | Guest response | Total time | Shutdown |
| --- | --- | --- | --- | --- |
| Normal 1 | Passed, exit 0 | 6.183 s | 55.230 s | HTTP 204, Firecracker 0 |
| Normal 2 | Passed, exit 0 | 4.610 s | 31.263 s | HTTP 204, Firecracker 0 |
| Control, 3 s deadline | Failed, exit 1 | Deadline exceeded | 24.184 s | HTTP 204, Firecracker 0 |

All four originals remained unchanged and no forced stop was needed in any of the three runs.

## Reader regressions

```bash
python3 -m unittest discover -s tests -v
```

Seven tests cover CR/LF variants and ANSI; a marker received in fragments; command echo; another run's identifier; a missing end marker; invalid UID/kernel/filesystem; and the historical excerpt. They do not require KVM and do not replace real boots.

## Findings kept visible

Consoles report I/O port address-range failures, `check access for rdinit=/init failed: -2, ignoring`, and a missing SELinux policy file. The guest continued through `/sbin/init` and responded to commands, but those messages were not fixed in this stage. The test validates boot and shutdown; it does not assert that every service is healthy or SELinux protection is active. Reviewed excerpts are included in the evidence JSON.

The structural checks from 2026-10-16 remain historical, without rerunning them in this stage: `e2fsck -f -n`, `gzip -t`, TAR listing and the local manifest's 23 hashes passed; binaries matched the distribution and the kernel had an x86-64 ELF header. The SquashFS superblock was inspected without validating all its contents. The local manifest does not independently authenticate artifact origin.

## Local files and limits

`console.log`, disks, binaries, run configurations and sockets stay in the local directory recorded for each result. The repository receives only reviewed summaries, hashes and a minimal excerpt of the earlier console. No keys are included in the evidence. Test copies remain available for inspection; remove them manually only after the processes have stopped.

Full artifact provisioning, networking, SSH, `jailer` and untrusted code execution remain untested. These instructions describe repeat runs with the existing prepared image and automatic root login. The repository remains private.
