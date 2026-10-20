# Firecracker in practice

[Português](README.md) · English

This lab started with a microVM that booted successfully while its controller reported missing guest evidence. We now have a repeatable test: copy the disk, start the VM, read responses through the console and stop it through the API, checking the original files afterwards.

This stage focuses on local boot on Ubuntu WSL2 with KVM. Networking, SSH and workloads belong to later exercises.

## What the test checks

- The VM uses a disk copy whose SHA256 matches the original before boot.
- The guest shell responds with its UID, kernel and `/dev/vda` mounted as writable ext4.
- Markers belong to the current run; an echoed command does not count as a response.
- The API accepts `SendCtrlAltDel` with HTTP 204, and Firecracker exits with code zero without a forced stop.
- The original disk, kernel, binary and configuration retain their hashes.

The reader normalizes CR/LF and removes ANSI colors, keeping the raw console in the local run directory. The script waits for the root prompt provided by this image's automatic login; it does not send another `root` or attempt authentication.

## Run it again

You need Linux x86-64, Python 3, read/write access to `/dev/kvm`, and an existing compatible Firecracker binary, kernel and rootfs. The guest needs automatic root login on the console, `id`, `uname` and `findmnt`. The source disk must be offline, with no other VM or mount using it. The script's lock is advisory and only coordinates tools that respect that lock.

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

Each run creates a unique `boot-*` directory containing `rootfs.ext4`, `vm-config.json`, `console.log` and `result.json`. Use a short path on the Linux filesystem for the Unix socket. Allow at least the disk size per run; copies remain available for inspection and cleanup is manual, with the VM stopped.

The default deadline for guest responses is 90 seconds, after copying and checking the disk. Shutdown gets another 30 seconds. Exit code `0` means the test passed; `1` means a failure recorded in `result.json`; `2` means a preparation/argument error. Preparation can fail before a report exists. The script does not retry failed tests.

To exercise a real deadline failure, repeat the command with `--boot-timeout 3`. This caused a failure in the documented environment but does not guarantee failure on every machine. The outcome must remain failed even if the VM shuts down correctly afterwards. [Procedure and results](docs/validation.en.md).

## Evidence and next steps

- [Boot validation, repeat run and failure control](docs/validation.en.md)
- [Reviewed results](evidence/boot-results.json)
- [Preserved earlier failure](evidence/historical-failure.json)
- [Next exercises](docs/roadmap.en.md)
- [Example configuration](config/vm-config.example.json)

Repeatability was validated using the existing local artifacts. Hashes identify those files; they do not independently authenticate their origin. The repository does not yet provision an image from scratch or distribute kernels, disks, binaries or keys. There is no GitHub Actions boot test at this stage.

The `jailer` has not been validated. This exercise does not establish sufficient isolation for untrusted code. Implementation and tests had assistance from the assistant; the evidence records what ran in the local environment. The repository remains private.

Reference: [official Firecracker v1.17.0 guide](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md). No license has been defined for this repository's own content; external components have separate licenses.
