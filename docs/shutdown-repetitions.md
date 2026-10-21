# Mais encerramentos sob carga, com o cache corrigido

[English](shutdown-repetitions.en.md) · [Investigação de memória](helper-memory.md) · [Resumo verificável](../evidence/shutdown-repetitions/summary.json)

Ampliamos a amostra depois de adotar a liberação do cache dos clones parados. Executamos três campanhas sequenciais, cada uma com seis repetições de quatro cenários: meia CPU e duas CPUs no encerramento, com e sem sondas. São 72 VMs novas, separadas dos resultados anteriores. O binário continua sendo o Firecracker oficial v1.17.0, estático musl, com a espera interna original de um segundo. Não injetamos pausas na vCPU nem adotamos a candidata de cinco segundos.

## Como foi feito

Cada VM faz boot real, confirma o teclado, executa dois loops de CPU no guest e passa pela tentativa controlada de ultrapassar o limite de tarefas. Dois workers externos disputam as CPUs 6 e 7 com o auxiliar. A ordem dos quatro cenários é invertida em repetições alternadas. As campanhas usam o lock do executor e não se sobrepõem.

O auxiliar mantém 768 MiB, sem swap, duas CPUs e 256 tarefas. Cada VM mantém 384 MiB no cgroup, zero swap, 32 tarefas e 64 descritores; o guest tem 256 MiB e duas vCPUs. Após o boot e após o teste de tarefas, conferimos seccomp, `NoNewPrivs`, UID/GID 65534, capacidades de execução zeradas, namespaces e raiz do jailer. Isso mantém as garantias já verificadas; o auxiliar de preparação continua privilegiado e os limites de segurança descritos no [guia do jailer](jailer.md) continuam aplicáveis.

Os controles não têm uprobes nem coleta de stacks. O observador de memória permanece ativo em todos os cenários, com intervalo alvo de 100 ms e marcas de fase. Nos instrumentados, as sondas acompanham envio, espera de resposta, saída da vCPU e `join`; as stacks são leituras sequenciais, sujeitas a erro quando a thread sai. Essa instrumentação consome CPU e afeta o tempo observado.

## Resultados

**72/72 encerramentos com código zero**, sem panic, timeout ou limpeza forçada. As 36 capturas instrumentadas passaram na revisão do protocolo e do escalonamento. Os 72 testes de isolamento e limite de tarefas passaram, os quatro artefatos originais permaneceram iguais e os 144 arquivos de clones tratados tiveram seus hashes conferidos depois das campanhas.

| Cenário | Amostras | Mínimo / mediana / máximo de shutdown (ms) |
| --- | ---: | ---: |
| Meia CPU, controle | 18 | 93,210 / 140,575 / 280,723 |
| Meia CPU, instrumentado | 18 | 125,477 / 195,200 / 330,106 |
| Duas CPUs, controle | 18 | 75,894 / 146,622 / 234,263 |
| Duas CPUs, instrumentado | 18 | 93,396 / 193,056 / 303,290 |

| Campanha | Pico do auxiliar (MiB) | Eventos no teto / OOM / OOM kill | PSI some / full (ms) | Direct reclaim (páginas) |
| --- | ---: | ---: | ---: | ---: |
| `jailer-tuksn0jb` | 326,75 | 0 / 0 / 0 | 30,380 / 30,380 | 0 |
| `jailer-mbk7ng3x` | 324,28 | 0 / 0 / 0 | 0,000 / 0,000 | 0 |
| `jailer-1ky6mv3w` | 322,23 | 0 / 0 / 0 | 1,398 / 0,590 | 0 |

O pico ficou acima dos 262,45 MiB da campanha corrigida anterior, ainda abaixo do teto de 768 MiB. Os incrementos de PSI não foram todos zero: preservamos 30,380 ms em `some` e `full` na primeira campanha, e 1,398 / 0,590 ms na terceira. Não atribuímos essas esperas pequenas a uma causa específica. Não houve direct reclaim registrado.

A maior espera de resposta do protocolo foi **8,128 ms**; o maior intervalo de `pthread_join`, **91 µs**. Das leituras de stacks, 3.595 não estavam vazias, 265 estavam vazias e 23 registraram `FileNotFoundError` ao acessar uma tarefa já ausente. Vazias e erros podem se sobrepor. Sete stacks não vazias do VMM ficaram inteiramente dentro de uma janela de espera de resposta; nenhuma foi capturada inteiramente dentro do `join`. Isso não significa ausência de atividade nessa etapa curta.

Os **31 testes automatizados** passaram. A primeira execução dos três testes novos encontrou `KeyError` no agregador com registros sintéticos sem campos opcionais. Corrigimos a normalização desses campos e repetimos a suíte; o executor das VMs não mudou. O [fechamento](../evidence/shutdown-repetitions/closing-check.json) registra o erro de desenvolvimento, a validação final e a ausência de containers, instâncias e sondas deixados por estas campanhas.

## O que os números permitem dizer

Esta campanha amplia a evidência de encerramento sob disputa de CPU com o cache corrigido. Não reproduzir o panic histórico não esclarece sua causa nem prova que ele foi eliminado. A duração total de shutdown inclui outras etapas e não deve ser comparada diretamente ao prazo interno de um segundo do canal.

A política só persiste e aconselha descarte do cache dos dois arquivos do clone depois de confirmar a VM parada. Os arquivos permanecem disponíveis. O pico pode variar por campanha: cache compartilhado, primeiro acesso e outras atividades do host influenciam a contabilidade. `DONTNEED` é uma solicitação ao kernel, sem garantia de liberação completa.

A janela de memória acaba após os cenários e a conferência das entradas, antes da serialização do arquivo do observador. A exportação e a releitura de integridade no host acontecem depois das três campanhas e ficam fora dessa janela. Campos de memória e leituras de stacks não são snapshots atômicos. As amostras não são ensaios independentes de produção; não calculamos garantia de confiabilidade a partir da ausência de falhas.

## Evidências e repetição

O [índice](../evidence/shutdown-repetitions/index.json) aponta os relatórios de memória e seus hashes. A pasta `protocol/` preserva as etapas, tempos, stacks simbolizadas, leituras vazias e erros; o [relatório de isolamento](../evidence/shutdown-repetitions/isolation-and-provenance.json) contém os snapshots e a comparação do código executado. Perfis completos ficam nos relatórios de protocolo. Discos, chaves e endereços absolutos das stacks não são publicados. A evidência anterior, incluindo falhas, permanece intacta.

Copie `config/jailer-profile.json` para um perfil local e acrescente:

```json
{
  "investigation_matrix": "host-load",
  "diagnostic_repetitions": 6,
  "diagnostic_pid_probe": true,
  "suite_timeout": 600
}
```

Esses campos complementam o perfil; mantenha os hashes, limites e `helper_cache_policy: "release-stopped-clones"`. Execute este comando três vezes, esperando cada campanha terminar e preservando todos os diretórios, inclusive se houver falha:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_memory --host-load \
  --profile /path/to/repetition-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results
```

Depois das campanhas, exporte os três diretórios completos:

```bash
python3 scripts/export_helper_memory.py \
  --runs /path/to/results/jailer-ONE /path/to/results/jailer-TWO /path/to/results/jailer-THREE \
  --output evidence/shutdown-repetitions
python3 scripts/export_shutdown_repetitions.py --evidence evidence/shutdown-repetitions
python3 -m unittest discover -s tests -v
```

O exportador de memória também atende ao A/B anterior; seu texto genérico menciona reter versus liberar cache. Aqui os três perfis usam a política corrigida, sem novo braço `retain`. O campo histórico `primary` do exportador de protocolo foi criado para campanhas de cinco repetições; a seleção desta amostra está em `campaigns.json` e no resumo de 72 casos. Se um executor for interrompido sem relatório final, preserve os arquivos e use a exportação parcial de protocolo; não apresente uma campanha incompleta como aprovada.
