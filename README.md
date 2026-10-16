# Firecracker Lab

Laborat?rio local de microVMs com Firecracker v1.17.0, Ubuntu WSL2 e KVM, em arquitetura x86-64.

## Estado verificado

- Artefatos existentes preservados; o projeto n?o foi recriado.
- Acesso de leitura e escrita ao KVM validado ap?s associa??o do usu?rio ao grupo `kvm`.
- Boot de uma ?nica microVM com c?pia do disco validado pelo console.
- Guest Ubuntu 24.04.5 LTS, kernel `6.18.54+`, sess?o root por login autom?tico.
- `/dev/vda` montado como ext4 grav?vel.
- Encerramento controlado por Ctrl+Alt+Del via API; Firecracker terminou com c?digo 0.
- Hash do disco original e conte?do da configura??o original preservados.

O controlador autom?tico de teste n?o reconheceu o marcador final devido ?s quebras de linha do console. Seu resultado registrou `Guest evidence incomplete`; as evid?ncias foram verificadas diretamente no console. A automa??o ainda precisa ser corrigida.

TAP, conectividade host/guest, SSH, NAT e restri??es de egress permanecem pendentes. N?o h? teste funcional de rede conclu?do.

## Arquitetura

```text
Windows -> Ubuntu WSL2 -> KVM -> Firecracker -> kernel + disco ext4 clonado
```

A configura??o testada usa 2 vCPUs e 1024 MiB de mem?ria, sem interface de rede declarada. O bin?rio `jailer` existe no laborat?rio, mas seu uso n?o foi validado.

## Conte?do deste reposit?rio

- `config/vm-config.example.json`: exemplo port?til baseado na configura??o testada.
- `docs/validation.md`: evid?ncias, limita??es e procedimento de valida??o.
- `docs/roadmap.md`: pr?ximos passos t?cnicos.

Discos, kernels, bin?rios, credenciais, sockets e logs brutos n?o s?o distribu?dos. Este reposit?rio documenta o laborat?rio existente; n?o cont?m instalador ou provisionamento completo.

## Pr?-requisitos

Linux x86-64 com KVM dispon?vel (no laborat?rio, Ubuntu WSL2), Firecracker compat?vel, kernel e rootfs preparados, Python 3 e ferramentas de inspe??o de arquivos e filesystems. O uso de WSL2 exige virtualiza??o disponibilizada ao Linux.

O dispositivo `/dev/kvm` deve permitir leitura e escrita ao usu?rio executor. No Ubuntu deste laborat?rio, ele pertence a `root:kvm`, modo `0660`. A associa??o administrativa ao grupo ? persistente e exige uma nova sess?o para atualizar os grupos efetivos. N?o use permiss?es globais `0666`.

## Configura??o

O exemplo usa caminhos relativos ao diret?rio de execu??o. N?o execute diretamente contra o disco original: prepare um diret?rio exclusivo e uma c?pia validada, e ajuste os caminhos na configura??o de teste conforme `docs/validation.md`.

O disco do exemplo ? grav?vel. Inicializar a VM altera a c?pia. Login autom?tico como root foi observado na imagem existente; antes de qualquer exposi??o de rede, revise esse comportamento e a configura??o SSH.

## Refer?ncias

- https://github.com/firecracker-microvm/firecracker
- https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md

N?o h? licen?a definida para o conte?do pr?prio deste reposit?rio. As licen?as dos componentes externos s?o independentes.
