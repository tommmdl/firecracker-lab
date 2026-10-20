# Next exercises

[Português](roadmap.md)

Boot now has a controller in the repository, console reader regressions and evidence from local runs. The earlier outcome remains recorded as an automation failure.

1. Completed: [build kernel/rootfs from pinned sources](build-from-source.en.md), with kernel signature verification and a pinned BusyBox checksum. Further work: package the host toolchain for stronger build reproducibility.
2. Inspect `fcnet.service`, automatic login and SSH configuration before adding networking.
3. Choose a subnet without conflicts and create a dedicated TAP, initially limited to host/guest traffic.
4. Validate key-based SSH and host identity with a dedicated `known_hosts` file.
5. Implement lab-scoped NAT and outbound rules, testing allowed and denied destinations and rollback.
6. Run a known task on a disposable disk and collect its exit code, logs and results.
7. Initial [jailer exercises completed](jailer.en.md): observed isolation and CPU/memory/task/descriptor/deadline enforcement. Next: less privileged bootstrap, seccomp policy, `CapBnd` and investigation of the retained shutdown panic.

Guest networking, SSH and firewall remain pending. Jailer has local exercises with explicit scope limits. Publishing the code does not change those components or validate isolation for untrusted code.

Alongside this route, we need to review CPU mitigations reported as vulnerable in the minimal guest. The [hardening summary](../README.en.md#hardening-still-to-do) collects the current limits.
