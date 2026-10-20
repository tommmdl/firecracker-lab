FROM rust@sha256:b5a086f64ffecaa4e283063184770107915756739598173e1f5712d6b34b84d0
RUN apt-get update && apt-get install -y --no-install-recommends libclang-dev musl-tools linux-libc-dev && rm -rf /var/lib/apt/lists/*
RUN rustup target add --toolchain 1.97.0 x86_64-unknown-linux-musl
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=cc
ENV CC_x86_64_unknown_linux_musl=musl-gcc
# Match the upstream development container's musl Linux-header layout.
RUN cd /usr/include/x86_64-linux-musl && ln -s ../x86_64-linux-gnu/asm asm && ln -s ../linux linux && ln -s ../asm-generic asm-generic
RUN rustup component add --toolchain 1.97.0 rustfmt
RUN apt-get update && apt-get install -y --no-install-recommends libseccomp-dev && rm -rf /var/lib/apt/lists/*
