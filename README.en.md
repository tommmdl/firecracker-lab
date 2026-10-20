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

- [Source build and verification](docs/build-from-source.en.md)
- [Boot validation, repeat run and failure control](docs/validation.en.md)
- [Reviewed results](evidence/boot-results.json)
- [Preserved earlier failure](evidence/historical-failure.json)
- [Next exercises](docs/roadmap.en.md)
- [Example configuration](config/vm-config.example.json)

The first stage used the existing Ubuntu artifacts; their hashes identify the files without authenticating their origin. The new recipe verifies Linux and BusyBox sources and builds a separate minimal image. The repository does not distribute kernels, disks, binaries or private keys. There is no GitHub Actions boot test at this stage.

The `jailer` has not been validated. This exercise does not establish sufficient isolation for untrusted code. Implementation and tests had assistance from the assistant; the evidence records what ran in the local environment. The code and reviewed evidence in this lab are public.

## Hardening still to do

The [real test records](evidence/source-build-results.json) include a retained shutdown failure, two passing boots after the correction and ten regression tests. They also record rejection of a modified source archive. These ran locally, without a GitHub Actions boot run.

Before running third-party workloads, the remaining work includes:

- Validate `jailer`, namespaces, cgroups and resource/time limits under failure. The VM already has configured vCPUs and memory, but that does not establish all those protections.
- Review the minimal kernel's CPU mitigations. Its console reported `Spectre V2 : User space: Vulnerable` and `Speculative Store Bypass: Vulnerable`; findings remain visible.
- Review the direct root shell before adding networking. TAP, key-based SSH and outbound rules with allow/deny tests have not been validated.
- Package the host toolchain and strengthen BusyBox origin verification. The kernel currently has a verified OpenPGP signature; BusyBox has a pinned SHA256 from official HTTPS.

The exercise demonstrates boot and shutdown in the recorded environment. Isolation for untrusted code remains unvalidated. [Build and limits](docs/build-from-source.en.md) · [Next exercises](docs/roadmap.en.md).

Reference: [official Firecracker v1.17.0 guide](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md). No license has been defined for this repository's own content; external components have separate licenses.
