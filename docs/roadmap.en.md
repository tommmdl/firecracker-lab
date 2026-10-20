# Next exercises

[Português](roadmap.md)

Boot now has a controller in the repository, console reader regressions and evidence from local runs. The earlier outcome remains recorded as an automation failure.

1. Completed: [build kernel/rootfs from pinned sources](build-from-source.en.md), with kernel signature verification and a pinned BusyBox checksum. Further work: package the host toolchain for stronger build reproducibility.
2. Inspect `fcnet.service`, automatic login and SSH configuration before adding networking.
3. Choose a subnet without conflicts and create a dedicated TAP, initially limited to host/guest traffic.
4. Validate key-based SSH and host identity with a dedicated `known_hosts` file.
5. Implement lab-scoped NAT and outbound rules, testing allowed and denied destinations and rollback.
6. Run a known task on a disposable disk and collect its exit code, logs and results.
7. Validate `jailer`, CPU/memory/time limits and failure behavior before moving to untrusted tasks.

Networking, SSH, firewall and `jailer` remain pending. This stage does not change those components or make the repository public.
