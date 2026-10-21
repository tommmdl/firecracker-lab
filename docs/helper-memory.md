# Por que o auxiliar chegou ao teto de memória?

[English](helper-memory.en.md) · [Protocolo e stacks](vcpu-protocol.md) · [Evidências](../evidence/helper-memory/index.json)

A investigação anterior registrou 16.722 eventos `memory.events.local:max` no auxiliar, sem OOM. Faltava saber o que estava ocupando a memória e em que etapa isso acontecia. Instrumentamos a matriz de shutdown e encontramos **cache das cópias dos discos e kernels acumulado entre cenários** como o principal componente observado. A correção reduz esse cache depois que cada VM termina, mantendo os arquivos e os limites existentes.

## O que medimos

O novo executor, `vcpu_memory`, usa a mesma matriz do protocolo: boot real, prontidão do teclado, dois loops no guest, teste de limite de tarefas e encerramento com meia CPU ou teto de duas CPUs, com e sem sondas. Dois workers sem privilégios disputam as mesmas CPUs 6 e 7. Cada rodada tem três repetições dos quatro braços, totalizando 12 VMs. As campanhas rodam em sequência, protegidas pelo lock do wrapper.

A cada etapa e com intervalo alvo de 100 ms, coletamos `memory.current`, `memory.peak`, `memory.stat`, eventos locais/hierárquicos e PSI do auxiliar e do supervisor. O pico do cgroup também cobre picos entre amostras. As leituras de campos diferentes não são atômicas; os timestamps e a cadência real ficam no arquivo bruto. `memory.events.local:max` conta ocorrências no limite, não bytes nem mortes de processo. PSI e contadores de reclaim ajudam a verificar se houve esperas e recuperação de páginas.

A janela termina depois dos cenários e da conferência dos hashes de entrada, antes de serializar o arquivo do próprio observador. A escrita desse arquivo e a exportação posterior ficam fora dos números da tabela.

O teto do auxiliar permaneceu em **768 MiB**, sem swap, com duas CPUs e 256 tarefas. O teto de memória de cada VM continuou em **384 MiB**, com zero swap, 32 tarefas e 64 descritores; o guest continua configurado com 256 MiB. UID/GID, capacidades de execução zeradas, `NoNewPrivs`, seccomp e namespaces do jailer seguem conferidos em todas as threads observadas.

## O que ficou retido

O código já calculava o hash do disco em blocos de 4 MiB; ele não carregava o arquivo inteiro em uma variável Python. Cada cenário, porém, copia um disco de 128 MiB e um kernel de aproximadamente 14,15 MiB. Essas cópias permanecem no resultado para auditoria. Encerrar a VM e remover seu cgroup não retira automaticamente o cache desses arquivos do cgroup do processo que os copiou.

Na primeira rodada, `jailer-lbymgyks`, o cache de arquivos após o primeiro encerramento era cerca de 147 MiB; após o segundo, 293 MiB. Ao final das 12 VMs, havia **686,47 MiB de arquivos** e **30,42 MiB de memória anônima**. O auxiliar chegou ao teto, registrou 7.891 eventos `max` e 254.475 páginas varridas por direct reclaim. Isso reproduziu a pressão sem reproduzir OOM ou o panic antigo da vCPU.

O perfil de uso, o crescimento por cenário e a redução medida ao atuar somente nos clones sustentam a atribuição ao cache. Não tratamos todo evento `max` como vazamento de heap ou como prova da causa do panic histórico.

## A correção adotada

Depois de confirmar que o VMM parou e foi coletado, o supervisor atua apenas nos arquivos **`rootfs.ext4` e `vmlinux` daquele cenário**:

1. Abre os arquivos sem truncar e rejeita links simbólicos ou caminhos fora da raiz esperada.
2. Executa `fsync` para persistir os dados e calcula o SHA256 em blocos.
3. Solicita `POSIX_FADV_DONTNEED` para o cache desses arquivos.
4. Preserva ambos os arquivos e registra identidade, tamanho, hash e duração da operação.

Não há `drop_caches` global, descarte dos arquivos de entrada, remoção dos discos ou aumento dos limites. A chamada é recusada se o VMM ainda estiver vivo. O conselho de descarte é **advisory**: retorno bem-sucedido não garante que o kernel libere todas as páginas. Por isso medimos o efeito no cgroup, além de registrar o retorno da chamada.

A política `helper_cache_policy: "release-stopped-clones"` foi adotada no perfil padrão e no perfil local usado para repetir o protocolo. Ela passa pelo encerramento compartilhado dos executores, incluindo os controles originais do jailer. Perfis históricos sem esse campo continuam com `retain`, permitindo reproduzir o comportamento antigo. Para voltar a ele, defina `helper_cache_policy: "retain"` no perfil explícito.

## Antes e depois

No par final, **24/24 VMs encerraram com código zero**, com seis capturas válidas de protocolo e stacks por política. O piloto anterior acrescentou 12 encerramentos limpos, fora dos números do par. Todas as verificações de isolamento após boot passaram; os quatro artefatos originais permaneceram iguais.

| Medida no auxiliar | Reter cache: `jailer-3u6_dwnp` | Liberar clones parados: `jailer-qz0gtv58` |
| --- | ---: | ---: |
| VMs / capturas instrumentadas | 12 / 6 | 12 / 6 |
| Teto de memória | 768 MiB | 768 MiB |
| Pico de memória | 768,00 MiB | 262,45 MiB |
| Memória total ao final | 738,08 MiB | 83,15 MiB |
| Cache de arquivos ao final | 693,68 MiB | 49,39 MiB |
| Memória anônima ao final | 30,98 MiB | 30,41 MiB |
| Incremento de `memory.events.local:max` | 7.434 | 0 |
| Páginas varridas por direct reclaim | 240.016 | 0 |
| PSI `some` / `full`, incremento total | 1,177182 s / 1,095044 s | 0 / 0 |
| OOM / mortes por OOM no auxiliar | 0 / 0 | 0 / 0 |

A redução do pico observado foi de cerca de **65,8%**, sem aumentar o teto. Em cada um dos 12 encerramentos corrigidos, o cache de arquivos caiu cerca de **142,15 MiB** entre as amostras antes/depois da operação. Os **24 arquivos tratados**, dois por VM, tiveram seus hashes conferidos novamente depois da campanha: todos iguais aos registrados antes do conselho de cache. Os discos continuam disponíveis para auditoria.

Os **28 testes automatizados** passaram. A adoção no perfil padrão também foi conferida com os seis controles originais do jailer, descritos abaixo.

A operação tem custo: na rodada corrigida, persistência, hashing e conselho de cache levaram de **0,424 a 0,608 s por VM**, com mediana de **0,522 s**, depois do encerramento. Esse tempo não faz parte do intervalo de shutdown medido. Não usamos a redução de memória para declarar ganho de latência.

O cache não ficou zerado: binários, traces e outros resultados ainda ocupam páginas. Novas cargas, arquivos maiores ou outra versão do kernel podem produzir outro resultado. A propriedade das páginas compartilhadas também depende de quem as acessou primeiro; são comparações locais, sem limpeza global de cache e sem promessa de benchmark universal.

## Dados e falhas continuam visíveis

As três campanhas completas passaram, incluindo as duas com pressão de memória. Esses eventos não foram apagados nem convertidos em OOM. A rodada inicial serve à investigação; o par `jailer-3u6_dwnp` / `jailer-qz0gtv58` usa o mesmo código executável e perfis diferentes somente na política de cache. As evidências incluem picos, PSI, reclaim, etapas antes/depois da liberação, protocolo, stacks vazias e erros de leitura quando as threads saem. Os resultados históricos permanecem publicados nos relatos anteriores.

A pressão de memória foi reduzida nesta amostra; **a causa do panic espontâneo antigo continua em aberto**. Não houve esse panic nas 36 VMs das três campanhas.

## Regressão e uma falha de prontidão preservada

A primeira conferência do executor original, [`jailer-ccq7jceq`](../evidence/helper-memory/regression/jailer-ccq7jceq.json), reprovou antes do boot: o socket já existia, mas a segunda thread ainda aparecia como `firecracker`, com `Seccomp=0`. O console registrou o início do servidor da API poucos milissegundos depois. O snapshot reprovado, a limpeza forçada e os hashes ficaram preservados.

A condição de prontidão agora exige observar `Name=fc_api`, `Seccomp=2` e `NoNewPrivs=1`, com prazo de cinco segundos e amostras registradas. O teste mantém o snapshot inicial e repete a inspeção completa antes de prosseguir. Se o filtro não aparecer, continua reprovando. Os testes de regressão rejeitam nome inicial, API sem filtro e `NoNewPrivs=0`.

A repetição [`jailer-d9gm_tlp`](../evidence/helper-memory/regression/jailer-d9gm_tlp.json) passou nos seis cenários com a política padrão nova. Baseline e repetição encerraram com código zero; o controle de seccomp desligado detectou a política negativa. Exaustão de descritores, OOM da VM e corte por prazo mantêm código `-9` e suas classificações esperadas. Eles não representam guests saudáveis. O contador local do auxiliar permaneceu com `max=0`, `oom=0` e `oom_kill=0`; os eventos hierárquicos incluem o OOM intencional da VM.

A correção de prontidão foi acrescentada depois do par de memória e validada nessa suíte original. As cópias exatas de `jailer_checks.py` e `jailer_runtime.py` usadas no A/B estão nas evidências; os módulos de cache e observação são os mesmos da comparação.

## Repetir

Crie dois perfis a partir de `config/jailer-profile.json`, com `investigation_matrix: "host-load"`, `suite_timeout: 600`, `diagnostic_repetitions: 3` e `diagnostic_pid_probe: true`. Use `helper_cache_policy: "retain"` em um e `"release-stopped-clones"` no outro. Execute um por vez:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_memory --host-load \
  --profile /caminho/perfil-retain-ou-release.json \
  --binary-dir /caminho/binarios-oficiais \
  --artifacts /caminho/artefatos \
  --output-parent /caminho/resultados

python3 scripts/export_helper_memory.py \
  --runs /caminho/resultados/jailer-ANTES /caminho/resultados/jailer-DEPOIS \
  --output evidence/helper-memory

python3 -m unittest discover -s tests -v
```

O exportador revisa os eventos do protocolo e, depois das campanhas, confere os hashes dos clones tratados contra os registrados antes do conselho de cache. Essa leitura de integridade acontece no host, fora das medições do auxiliar. Os arquivos brutos e discos permanecem locais; a cópia pública inclui campos revisados e seus hashes, sem chaves ou endereços absolutos das stacks.

Referências: [contabilidade, eventos e PSI do cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), [`POSIX_FADV_DONTNEED` e persistência prévia](https://man7.org/linux/man-pages/man2/posix_fadvise.2.html).
