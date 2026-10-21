# Firecracker na prática

Português · [English](README.en.md)

Este lab começou com uma microVM que já dava boot, mas cuja automação dizia que faltava evidência do guest. Agora temos um teste repetível: copiar o disco, subir a VM, ler respostas pelo console e encerrar pela API, conferindo os arquivos originais no final.

A etapa seguinte já acrescenta uma receita para compilar Linux 6.12.113 e BusyBox 1.38.0 a partir de fontes fixados e montar um disco novo. [Construir kernel e imagem do zero](docs/build-from-source.md). O foco continua no boot local em Ubuntu WSL2 com KVM; rede, SSH e workloads ficam para os próximos exercícios.

## O que o teste confere

- O disco usado pela VM é uma cópia com SHA256 igual ao original antes do boot.
- O shell do guest responde com UID, kernel e montagem de `/dev/vda` como ext4 gravável.
- Os marcadores pertencem à execução atual; o eco do comando não conta como resposta.
- A API aceita `SendCtrlAltDel` com HTTP 204, e o Firecracker termina com código zero, sem encerramento forçado.
- Disco, kernel, binário e configuração originais mantêm os hashes.

A leitura normaliza CR/LF e remove cores ANSI, preservando o console bruto no diretório local da execução. O script espera o prompt root que esta imagem oferece por login automático; não envia outro `root` nem tenta autenticar.

## Como repetir

Você precisa de Linux x86-64, Python 3, acesso de leitura e escrita a `/dev/kvm`, Firecracker, kernel e rootfs compatíveis já preparados. O guest precisa de login automático root no console, `id`, `uname` e `findmnt` ou as consultas a procfs/sysfs usadas pela imagem mínima. O disco de origem deve estar offline, sem outra VM ou montagem usando-o. O lock do script é consultivo e só coordena ferramentas que respeitam esse lock.

No ambiente existente, os artefatos ficam em `$HOME/fc-lab`. Ajuste os caminhos para o seu ambiente:

```bash
python3 -m unittest discover -s tests -v

python3 scripts/boot_check.py \
  --firecracker "$HOME/fc-lab/firecracker" \
  --kernel "$HOME/fc-lab/vmlinux" \
  --rootfs "$HOME/fc-lab/rootfs.ext4" \
  --config "$HOME/fc-lab/vm-config.json" \
  --output-parent "$HOME/fc-lab"
```

O exemplo em `config/` acompanha a imagem mínima nova; a configuração Ubuntu original fica preservada no diretório local.

Cada execução cria uma pasta `boot-*` exclusiva, com `rootfs.ext4`, `vm-config.json`, `console.log` e `result.json`. Use um caminho curto no filesystem Linux para o socket Unix. Reserve pelo menos o tamanho do disco por execução; as cópias são mantidas para inspeção e a limpeza é manual, com a VM parada.

O tempo padrão para obter as respostas é 90 segundos, depois de copiar e conferir o disco. O encerramento tem mais 30 segundos. Código de saída `0` significa teste aprovado; `1`, falha registrada em `result.json`; `2`, erro de preparação/argumentos. Falhas de preparação podem acontecer antes de existir um relatório. O script não repete um teste que falhou.

Para exercitar uma falha real de prazo, repita o comando com `--boot-timeout 3`. Esse valor causou falha no guest Ubuntu da primeira etapa, mas não garante falha na imagem mínima nova ou em outra máquina. O resultado deve continuar reprovado mesmo que a VM encerre corretamente depois. [Procedimento e resultados](docs/validation.md).

## Evidências e próximos passos

- [Jailer: isolamento, recursos e falhas reais](docs/jailer.md)
- [Construção a partir dos fontes e verificações](docs/build-from-source.md)
- [Validação do boot, repetição e controle de falha](docs/validation.md)
- [Resultados revisados](evidence/boot-results.json)
- [Falha anterior preservada](evidence/historical-failure.json)
- [Próximos exercícios](docs/roadmap.md)
- [Configuração de exemplo](config/vm-config.example.json)

Os resultados da primeira etapa usam os artefatos Ubuntu existentes; seus hashes identificam os arquivos, sem autenticar a origem. A nova receita verifica os fontes do kernel e do BusyBox e constrói uma imagem mínima separada. O repositório não distribui kernel, disco, binários ou chaves privadas. Não há teste de boot no GitHub Actions nesta etapa.

O `jailer` agora tem ensaios locais de isolamento e recursos descritos no [guia próprio](docs/jailer.md). A avaliação de isolamento para código não confiável continua incompleta. A implementação e os testes tiveram ajuda do assistente; as evidências registram o que foi executado no ambiente local. O código e as evidências revisadas deste lab são públicos.

## O que já foi exercitado com o jailer

O [perfil final](config/jailer-profile.json) passou em duas execuções de seis cenários. Os testes observaram UID/GID 65534, capacidades de execução zeradas, raiz e namespaces separados e seccomp ativo nas threads após o boot. Carga real conferiu CPU, memória, tarefas, descritores e prazo. Os controles com seccomp desligado, OOM e prazo não são perfis ou guests aprovados.

O panic antigo de shutdown continua preservado. A [investigação](docs/shutdown-investigation.md) localizou o SIGSEGV no caminho de aborto da musl em um controle, corrigiu a prontidão do teclado e a coleta do rastreador e comparou dois binários musl estáticos sob carga no host. Depois dessas correções, foram dez encerramentos limpos por variante. A candidata de cinco segundos tolerou uma pausa de 1,5 segundo na vCPU e ainda falhou no controle longo; continua experimental, com a causa do atraso histórico em aberto. [Resultados anteriores](evidence/jailer-results.json) · [Falhas preservadas](evidence/shutdown-investigation.json).

A [captura de escalonamento e sinais](docs/vcpu-scheduling.md) acrescentou 24 encerramentos limpos sob carga, com 12 traces válidos, controles sem captura e um teste de limite de tarefas. A maior espera pronta observada foi 13,262 ms; o panic histórico não reapareceu e sua causa continua em aberto. Tentativas inválidas e interrompidas também estão publicadas.

A [investigação do protocolo e das stacks do kernel](docs/vcpu-protocol.md) acompanha o envio, a espera do canal e o `join` no binário oficial. A rodada final teve 20 encerramentos limpos, dez capturas instrumentadas e stacks reais em futex/KVM. A espera pelo canal chegou a 5,867 ms, e o `join`, a 83 µs; a causa do panic antigo continua aberta. Falhas de preparação, do validador e de leitura também estão publicadas.

A [investigação de memória do auxiliar](docs/helper-memory.md) identificou cache de clones acumulado e adotou liberação após cada VM parada. No A/B sob carga, o pico caiu de 768 para 262,45 MiB e os eventos no teto passaram de 7.434 para zero, com os mesmos limites. Foram 24 encerramentos limpos no par, hashes preservados e 28 testes automatizados aprovados. A regressão também preserva uma falha de prontidão da API e sua correção.

## O que ainda falta de hardening

Os [testes reais](evidence/source-build-results.json) incluem uma falha de shutdown preservada, dois boots aprovados depois da correção e dez testes de regressão. Também registram a rejeição de um fonte alterado. Foram executados localmente, sem boot no GitHub Actions.

Antes de executar workloads de terceiros, ainda falta:

- Reduzir os privilégios do bootstrap usado nos ensaios do `jailer`, revisar `CapBnd` e a política de seccomp e ampliar a validação em ambiente dedicado. O auxiliar atual é privilegiado; UTS, IPC, user e cgroup namespaces continuam compartilhados com ele.
- Revisar as mitigações de CPU do kernel mínimo. O console reportou `Spectre V2 : User space: Vulnerable` e `Speculative Store Bypass: Vulnerable`; os achados continuam visíveis.
- Rever o shell root direto antes de acrescentar rede. TAP, SSH por chave e regras de saída com testes de permitido/negado ainda não foram validados.
- Encapsular o toolchain do host e fortalecer a verificação de origem do BusyBox. Hoje o kernel tem assinatura OpenPGP validada; o BusyBox tem SHA256 fixado a partir do HTTPS oficial.

O exercício comprova boot e encerramento no ambiente registrado. O isolamento completo para código não confiável continua sem comprovação. [Construção e limites](docs/build-from-source.md) · [Próximos exercícios](docs/roadmap.md).

Referência: [guia oficial do Firecracker v1.17.0](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md). Não há licença definida para o conteúdo próprio; as licenças dos componentes externos são independentes.
