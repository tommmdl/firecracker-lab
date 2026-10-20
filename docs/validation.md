# Como validamos o boot

[English](validation.en.md) · [Voltar ao README](../README.md)

## O problema anterior

Em 16/10/2026, o Firecracker encerrou com código zero e o console mostrou UID root, kernel `6.18.54+` e `/dev/vda` como ext4 gravável. Mesmo assim, o resultado do controlador foi `Guest evidence incomplete`.

O console preservado contém os marcadores de início e fim, com `CR CR LF` nas linhas. Uma busca literal por `\nAUDIT_GUEST_END\n` não encontra esse trecho bruto. A normalização resolve essa diferença. Também havia um `root` enviado depois do login automático, causando `-bash: root: command not found`.

O código do controlador antigo não estava guardado no repositório. Não afirmamos ter reexecutado esse controlador: o teste de regressão usa um trecho real e revisado do console para reproduzir a diferença de enquadramento das linhas. O [resultado antigo](../evidence/historical-failure.json) continua registrado como falha da automação, mesmo com evidências manuais de boot.

## O controlador novo

O [script](../scripts/boot_check.py) usa somente a biblioteca padrão do Python. Ele cria um diretório exclusivo, calcula hashes dos quatro artefatos originais, copia o disco e confere a cópia antes de iniciar uma única VM. A configuração gerada aponta para a cópia; a original não é editada.

Um pseudo-terminal recebe os bytes do console. A interpretação normaliza CR/LF e remove sequências ANSI; o log bruto permanece intacto. O controlador aguarda o prompt root e envia uma consulta com um identificador aleatório por execução. Só aceita marcadores completos em linhas próprias e respostas válidas entre eles. O eco do comando não passa nessa checagem.

O guest precisa responder com UID `0`, um valor de kernel e montagem `/dev/vda ext4` contendo a opção `rw`. Em seguida, o controlador envia `SendCtrlAltDel` pela API no socket Unix e exige HTTP 204 e saída zero. Se o prazo de encerramento vencer, termina somente o processo que criou e reprova o teste. A preservação dos quatro hashes também é obrigatória.

## Execuções reais desta etapa

Os [resultados revisados](../evidence/boot-results.json) registram as duas execuções aprovadas e o controle com prazo reduzido, junto aos hashes e aos avisos extraídos dos consoles. Os tempos até a resposta do guest usam relógio monotônico do controlador, começando depois da cópia e dos hashes iniciais; não são um benchmark de inicialização do kernel. O tempo total inclui preparação e conferência final.

As duas execuções normais começaram de cópias novas do mesmo disco original. Os testes usam Firecracker v1.17.0, 2 vCPUs, 1024 MiB e nenhuma interface de rede configurada. O guest é Ubuntu 24.04.5 LTS com kernel `6.18.54+`.

O controle usa o mesmo comando do README com `--boot-timeout 3`. Esperamos saída `1` e ausência de evidência completa dentro do prazo. Um shutdown bem-sucedido depois do timeout não transforma o teste em sucesso. Esse prazo foi escolhido para este ambiente; uma máquina mais rápida pode exigir outro valor para reproduzir a falha.


| Execução | Resultado do controlador | Resposta do guest | Tempo total | Shutdown |
| --- | --- | --- | --- | --- |
| Normal 1 | Aprovado, saída 0 | 6,183 s | 55,230 s | HTTP 204, Firecracker 0 |
| Normal 2 | Aprovado, saída 0 | 4,610 s | 31,263 s | HTTP 204, Firecracker 0 |
| Controle, prazo de 3 s | Reprovado, saída 1 | Prazo excedido | 24,184 s | HTTP 204, Firecracker 0 |

Os quatro originais ficaram intactos e nenhum encerramento foi forçado nas três execuções.

## Regressões da leitura

```bash
python3 -m unittest discover -s tests -v
```

Naquela etapa foram sete testes: variações de CR/LF e ANSI; marcador recebido em partes; eco do comando; identificador de outra execução; ausência do marcador final; UID/kernel/filesystem inválidos; e o trecho histórico. Não exigem KVM e não substituem os boots reais.

## O que ficou visível

Os consoles registram falhas de acesso a faixas de portas de I/O, `check access for rdinit=/init failed: -2, ignoring` e ausência de arquivo de política SELinux. O guest prosseguiu pelo `/sbin/init` e respondeu aos comandos, mas essas mensagens não foram corrigidas nesta etapa. O teste valida boot e encerramento; não declara saúde de todos os serviços nem proteção SELinux ativa. Os trechos revisados constam na evidência JSON.

As verificações estruturais de 16/10/2026 continuam sendo histórico, sem nova execução nesta etapa: `e2fsck -f -n`, `gzip -t`, listagem TAR e os 23 hashes do manifesto local passaram; binários coincidiam com a distribuição e o kernel tinha cabeçalho ELF x86-64. O superbloco SquashFS foi inspecionado, sem validar seu conteúdo integral. O manifesto local não autentica independentemente a origem.

## Arquivos locais e limites

`console.log`, discos, binários, configurações de execução e sockets ficam no diretório local indicado em cada resultado. O repositório recebe apenas resumos revisados, hashes e um trecho mínimo do console antigo. Nenhuma chave entra nas evidências. As cópias dos testes ficam preservadas para inspeção; remova-as manualmente apenas com os processos parados.

Esta etapa foi seguida pela [receita de construção a partir dos fontes](build-from-source.md), com guest mínimo e dez testes de leitura/entradas. Rede, SSH, `jailer` e execução de código não confiável continuam sem validação. A documentação descreve repetição com a imagem já preparada, com login automático root. O repositório permanece privado.
