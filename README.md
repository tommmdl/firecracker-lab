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

- [Construção a partir dos fontes e verificações](docs/build-from-source.md)
- [Validação do boot, repetição e controle de falha](docs/validation.md)
- [Resultados revisados](evidence/boot-results.json)
- [Falha anterior preservada](evidence/historical-failure.json)
- [Próximos exercícios](docs/roadmap.md)
- [Configuração de exemplo](config/vm-config.example.json)

Os resultados da primeira etapa usam os artefatos Ubuntu existentes; seus hashes identificam os arquivos, sem autenticar a origem. A nova receita verifica os fontes do kernel e do BusyBox e constrói uma imagem mínima separada. O repositório não distribui kernel, disco, binários ou chaves privadas. Não há teste de boot no GitHub Actions nesta etapa.

O `jailer` ainda não foi validado. Este exercício não demonstra isolamento suficiente para código não confiável. A implementação e os testes tiveram ajuda do assistente; as evidências registram o que foi executado no ambiente local. O repositório permanece privado.

Referência: [guia oficial do Firecracker v1.17.0](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/docs/getting-started.md). Não há licença definida para o conteúdo próprio; as licenças dos componentes externos são independentes.
