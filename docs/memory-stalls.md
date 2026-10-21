# De onde vêm as pequenas esperas de memória?

[English](memory-stalls.en.md) · [Repetições anteriores](shutdown-repetitions.md) · [Evidências](../evidence/memory-stalls/attribution-index.json)

Na amostra anterior, duas campanhas registraram pequenos incrementos de PSI de memória, mesmo com o cache corrigido e sem atingir o teto do auxiliar. Revimos todas as janelas com incremento e acrescentamos coleta de I/O, contadores do host e stacks nos pontos de entrada e saída de `psi_memstall` do kernel. A ideia foi observar os caminhos envolvidos antes de propor outra mudança de comportamento.

## O que foi acrescentado

O executor `vcpu_pressure` mantém o boot real, prontidão do teclado, loops de CPU no guest, teste de tarefas e shutdown da matriz existente. Acrescenta PSI de memória/I/O/CPU, `cpu.stat`, `io.stat`, faltas de página, refaults e contadores de recuperação de páginas nos cgroups do auxiliar, supervisor e conjunto das VMs. Também lê contadores globais do host, incluindo alocação e compactação. Eles incluem outras cargas e não medem exclusivamente o lab.

Uma instância própria de tracefs registra `psi_memstall_enter` e `psi_memstall_leave`, com stacks. O PID do auxiliar no domínio do rastreador é identificado por um marcador exclusivo; `set_event_pid` e `event-fork` restringem os registros ao auxiliar e seus descendentes futuros. As sondas são registradas no kernel compartilhado e têm custo de instrumentação, embora os registros sejam filtrados. A limpeza remove somente a instância e as duas sondas criadas por este ensaio. Não há `ptrace`, pausa injetada, alteração de `drop_caches`, leitura de chaves ou modificação dos arquivos originais.

Os limites continuam: auxiliar com 768 MiB, sem swap, duas CPUs e 256 tarefas; cada VM com 384 MiB no cgroup, zero swap, 32 tarefas e 64 descritores. O guest mantém duas vCPUs e 256 MiB. Dois workers sem privilégios disputam as CPUs 6 e 7. O Firecracker oficial v1.17.0 continua com o prazo interno original de um segundo. UID/GID, seccomp, `NoNewPrivs`, capacidades de execução, namespaces e raiz do jailer são conferidos após boot e após o teste de tarefas. As [limitações de segurança existentes](jailer.md) continuam valendo.

## O que encontramos

Na maior rodada anterior, 29,593 dos 30,380 ms de PSI `some` apareceram nas janelas de inicialização e preparação da primeira VM. A outra rodada com incremento teve eventos mais tarde. As evidências históricas não têm stacks de `psi_memstall` nem PSI de I/O; não atribuímos retrospectivamente uma causa a cada uma dessas esperas.

| Rodada | Sondas novas de memória | VMs concluídas / previstas | Status da campanha | PSI memória some / full (ms) | Pico (MiB) |
| --- | --- | ---: | --- | ---: | ---: |
| Piloto: `jailer-qcvjl_tt` | sim | 4 / 4 | aprovada | 189,970 / 167,363 | 255,05 |
| Ligadas: `jailer-1zzwgi68` | sim | 12 / 12 | aprovada | 0,000 / 0,000 | 285,68 |
| Desligadas, antes da correção de PID: `jailer-c1sxrbwi` | não | 10 / 12 | **falhou** | 127,092 / 127,092 | 269,82 |
| Ligadas novamente: `jailer-e5829fe6` | sim | 12 / 12 | aprovada | 58,791 / 58,104 | 288,74 |
| Desligadas, após correção de PID: `jailer-0d2gc21s` | não | 12 / 12 | aprovada | 2,871 / 2,871 | 286,46 |

Os **50 encerramentos concluídos foram limpos**, sem panic nem timeout; isso não transforma a campanha interrompida em sucesso. Houve uma falha de preparação e um cenário final não executado. As 25 capturas de protocolo/escalonamento passaram na revisão dos casos concluídos. Os quatro artefatos originais permaneceram iguais e os **100 arquivos de clones tratados** mantiveram seus hashes. Os dois arquivos do clone que não chegou ao boot também permaneceram iguais às entradas.

Todos os auxiliares destas cinco rodadas mantiveram `max=0`, `oom=0` e `oom_kill=0`, com swap observado igual a zero. O pico máximo foi 288,74 MiB, abaixo do teto de 768 MiB. Isso não significa ausência de pressão no host.

No piloto, as stacks mostraram readahead/refault e **90 entradas no caminho lento de alocação durante a janela de memória**, todas na thread principal do auxiliar. A stack de alocação passa por `do_sendfile`, escrita ext4 e alocação de folio, compatível com a cópia dos clones. A maior parcela de PSI `some` do piloto, 175,935 ms, ficou na preparação do primeiro clone. Essa reprodução registrou uma espera maior que as pequenas esperas anteriores e permanece na tabela.

Na terceira campanha, foram **três entradas em alocação lenta dentro da janela**, também na thread principal; outras oito ocorreram depois dela, na finalização dos relatórios. O trace completo contém ainda 602 entradas por readahead. O cgroup registrou 3.221 refaults de arquivos e 2.496 páginas varridas por `kswapd`, com `pgscan_direct=0`. No host, no mesmo período, houve incremento de 277 páginas em `pgscan_direct`, duas ocorrências de `allocstall_movable`, três de `compact_stall` e uma página de swap-in. Esses contadores globais incluem outras cargas; não atribuímos o swap ao guest, cujos limites de swap continuam em zero.

Uma campanha instrumentada teve PSI de memória zero e captura válida sem eventos de `psi_memstall`, mas **450,166 ms de PSI de I/O `some`**. Portanto, não convertemos todo tempo de I/O em espera de memória. A repetição sem as sondas novas terminou com 2,871 ms de PSI de memória. A sequência não prova que ligar sondas aumenta ou reduz PSI: a condição do host e do cache mudou entre rodadas.

As três capturas novas (piloto e duas campanhas com sondas) tiveram zero eventos perdidos nos buffers e limpeza confirmada. Preservamos também a captura válida vazia. `io.stat` existe na raiz do auxiliar, mas ficou indisponível no supervisor e no cgroup pai das VMs, pois o controlador de I/O não foi delegado aos filhos. Os erros `FileNotFoundError` e sua contagem aparecem nos relatórios, sem serem convertidos em bytes zero. PSI de I/O nesses filhos continuou disponível.

### Uma falha real de preparação

A campanha `jailer-c1sxrbwi` parou após dez VMs, com `ValueError: invalid literal for int() with base 10: ''`. A tentativa `boost-control-03` deixou console de início da API e um arquivo de PID que, depois, continha `343`. O leitor anterior fazia `int(pidfile.read_text())` assim que o arquivo existia. A corrida entre criação e publicação do conteúdo é inferida desse erro e do ponto de leitura; não temos uma captura do arquivo vazio naquele instante. Nenhum boot desse cenário foi validado. A limpeza do cgroup foi confirmada e o último braço não chegou a ser iniciado.

O leitor agora aguarda arquivo ausente ou vazio, dentro do mesmo prazo de dez segundos. Conteúdo inválido continua reprovando; PID, socket e todas as inspeções posteriores continuam obrigatórios. Dois testes cobrem criação vazia seguida de publicação e rejeição de conteúdo malformado. Na repetição real, oito das doze VMs passaram por leituras pendentes (ausentes **ou** vazias), totalizando onze tentativas; não distinguimos esses dois estados no contador. As doze completaram boot, isolamento e shutdown.

Os **39 testes automatizados** e os [seis controles originais](../evidence/memory-stalls/regression/jailer-quppavgn.json) passaram após a correção. OOM da VM, exaustão de descritores, corte por prazo e seccomp desligado continuam controles negativos com suas classificações esperadas, sem serem apresentados como guests saudáveis. As três campanhas iniciais têm o mesmo código executado; antes da repetição mudaram apenas o leitor de PID e sua chamada. As versões anteriores do piloto e do leitor estão preservadas junto aos hashes. O [fechamento](../evidence/memory-stalls/closing-check.json) registra isolamento, procedência e ausência de containers, instâncias ou sondas deixados pelo ensaio.

## Como interpretar

PSI de memória não equivale ao contador de ocorrências em `memory.max`, a OOM ou ao tempo de uma única chamada. A [documentação de PSI](https://docs.kernel.org/accounting/psi.html) descreve os totais em microssegundos de estados de espera, com visões `some` e `full`. No [readahead do Linux 6.18](https://github.com/torvalds/linux/blob/v6.18/mm/readahead.c), a recuperação de páginas do workingset também pode entrar em `psi_memstall`; portanto, um incremento não prova exaustão do limite do container. O [alocador de páginas](https://github.com/torvalds/linux/blob/v6.18/mm/page_alloc.c) fornece outro caminho para esse estado. Essas fontes upstream ajudam a interpretar as stacks; não substituem a reprodução exata do kernel WSL 6.18.54.1 deste host.

As stacks identificam os caminhos que passaram pelo ponto instrumentado nesta reprodução. Uma chamada à função não significa necessariamente espera contabilizada positiva; chamadas aninhadas e o próprio rastreador também influenciam a observação. Não somamos a diferença entre entradas e saídas para inventar um total de PSI. A correlação de eventos com janelas de incremento é temporal, com leituras sequenciais e possível atraso de contabilização.

Os testes usam duas dimensões: metade das VMs tem as sondas anteriores de protocolo e coleta de stacks por procfs, e metade não. Separadamente, ligamos ou desligamos as novas sondas de memória para a campanha inteira. Todas mantêm observação de cgroups. As campanhas são sequenciais e não limpam caches globais; não constituem uma comparação causal isolada de efeito do rastreador ou aquecimento de cache.

A janela de memória começa depois dos imports Python e termina antes da serialização do seu próprio arquivo. O trace tem janela um pouco maior e inclui a serialização posterior. Os relatórios preservam todos os eventos, mas distinguem aqueles que coincidem com intervalos de incremento. Contadores de cgroups filhos deixam de existir após sua remoção; não tratamos essa ausência como zero. `memory.stat` e PSI podem ser atualizados em momentos diferentes. Discos e relatórios completos ficam locais, com hashes e campos revisados publicados; stacks públicas preservam símbolos e retiram endereços absolutos.

## O que mudamos e o que falta

Mantivemos a liberação do cache dos clones somente após a VM parar, com persistência e hashes. Esta investigação acrescenta diagnóstico opcional; não aumenta limites, não desliga segurança e não adota uma otimização com base em uma única rodada. Para atribuir cada espera pequena da amostra antiga seria necessário ter capturado as stacks naquela execução. Para testar uma mitigação específica de alocação ou leitura, ainda falta um experimento com a mesma condição inicial reproduzível e uma intervenção bem delimitada. O panic histórico da vCPU continua sem causa esclarecida.

## Repetir

Copie `config/jailer-profile.json` para um perfil local. Mantenha hashes, limites e `helper_cache_policy: "release-stopped-clones"`. Acrescente:

```json
{
  "investigation_matrix": "host-load",
  "diagnostic_repetitions": 3,
  "diagnostic_pid_probe": true,
  "suite_timeout": 600,
  "memory_stall_trace": true
}
```

Execute uma campanha por vez, com `memory_stall_trace` em `true`, `false` e `true`, preservando todos os diretórios e falhas. O campo altera somente as sondas novas de memória; a matriz de protocolo permanece igual.

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_pressure --host-load \
  --profile /path/to/pressure-profile.json \
  --binary-dir /path/to/official-binaries \
  --artifacts /path/to/artifacts \
  --output-parent /path/to/results
```

Após terminar as campanhas, exporte os diretórios do piloto e das três rodadas, além dos diretórios históricos se estiverem disponíveis:

```bash
python3 scripts/export_helper_memory.py \
  --runs /path/to/PILOT /path/to/ON /path/to/OFF /path/to/ON-AGAIN \
  --output evidence/memory-stalls
python3 scripts/export_pressure_evidence.py \
  --runs /path/to/PILOT /path/to/ON /path/to/OFF /path/to/ON-AGAIN \
  --historical /path/to/HISTORICAL-ONE /path/to/HISTORICAL-TWO /path/to/HISTORICAL-THREE \
  --output evidence/memory-stalls
python3 -m unittest discover -s tests -v
```

Os exportadores completos exigem relatório final. Se houver interrupção, preserve os arquivos e use a exportação parcial do protocolo; uma captura ausente ou com perda de eventos não equivale a ausência de espera. O índice genérico do exportador de memória ainda descreve o A/B anterior; nesta investigação todos usam a política corrigida. O piloto precede a inclusão de contadores do host e das confirmações adicionais de opções do trace; fica identificado separadamente.
