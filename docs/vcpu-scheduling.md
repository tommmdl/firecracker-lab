# Onde a vCPU ficou esperando?

[English](vcpu-scheduling.en.md) · [Investigação do shutdown](shutdown-investigation.md) · [Evidências](../evidence/scheduling/index.json)

A etapa seguinte acrescenta [sondas do protocolo e stacks do kernel](vcpu-protocol.md): vinte encerramentos limpos, dez capturas com envio, retorno do canal e `join`, além dos erros da própria investigação preservados. O panic antigo continua sem recorrência.

Capturamos o escalonamento, os sinais e as entradas e saídas de KVM durante encerramentos sob carga no host. O objetivo era observar um atraso espontâneo sem pausar a vCPU com um depurador. **O panic histórico não reapareceu nas rodadas finais.** Os eventos explicam os encerramentos desta amostra, mas não fecham a causa daquela execução antiga.

## Como o teste foi montado

Usamos o Firecracker **oficial v1.17.0**, com o prazo interno de um segundo, e os mesmos kernel e disco construídos no lab. A candidata de cinco segundos não substituiu esse binário. Os hashes dos quatro artefatos são conferidos antes e depois de cada campanha.

Duas tarefas de CPU rodam em um contêiner separado, sem privilégios, nas mesmas CPUs **6 e 7** do auxiliar das VMs. A carga tem teto de duas CPUs, 64 MiB e oito tarefas. O auxiliar continua limitado a duas CPUs, 768 MiB e 256 tarefas; é privilegiado para preparar namespaces, cgroups e a captura. Esse privilégio pertence ao auxiliar de confiança, não à microVM.

Cada repetição alterna quatro braços: encerramento com meia CPU ou com teto de duas CPUs, com captura ou sem captura. A ordem se inverte na repetição seguinte. Todos fazem boot, confirmam o teclado e executam dois loops no guest por dois segundos. Mantivemos a parada histórica dos loops sem confirmação de `wait`. Uma segunda campanha também tenta criar 48 threads num processo de teste dentro do cgroup da VM, espera a saída das threads criadas e confere novamente o isolamento. O limite de 32 tarefas precisa bloquear essa tentativa.

As VMs continuam com UID/GID 65534, capacidades efetivas zeradas, `NoNewPrivs`, seccomp, namespaces de PID/mount/rede do jailer, disco copiado por cenário e limites de memória, tarefas e descritores. Cada execução verifica o perfil em todas as threads observadas. Não removemos isolamento para obter um encerramento limpo.

## Capturar sem congelar a thread

O [supervisor](../scripts/vcpu_scheduling.py) monta tracefs somente no auxiliar e cria uma **instância exclusiva** com nome aleatório. Não muda as opções nem o buffer da instância global. Durante os loops, descobre os IDs dos eventos por `fc_vcpu 0` e `fc_vcpu 1`; exige um único ID para cada nome. Outras microVMs Firecracker não devem rodar durante essa descoberta: um resultado ambíguo reprova a captura.

Depois, restringe os eventos aos dois IDs numéricos:

- `sched_switch`, `sched_waking`, `sched_wakeup` e `sched_process_exit`;
- `signal_generate` e `signal_deliver`;
- `kvm_entry` e `kvm_exit`.

Usa relógio `mono`, buffer de 1 MiB por CPU e marcadores de início/fim. A janela começa imediatamente antes de aplicar a cota de encerramento e chamar a API; os eventos são coletados até observar a saída do VMM. O buffer precisa ter eventos reais de escalonamento, geração/entrega do sinal 35 e saída de **cada** vCPU, sem overruns nem eventos descartados. O supervisor desliga os eventos e remove sua própria instância ao terminar. Não usa `ptrace` nem injeta pausas.

A espera por CPU é calculada de um `sched_wakeup` ou de uma saída de CPU em estado `R` até a próxima entrada da mesma thread. Um intervalo dormindo em `S` não entra nessa conta. Intervalos cortados pelo início ou fim da captura não têm duração completa e não entram no máximo. Essa medida reúne contenção e eventuais restrições de CPU; não atribui sozinha cada microssegundo a uma delas.

## Resultados finais

As campanhas sequenciais `jailer-ivyhqnjk` e `jailer-phr137uc` tiveram **24/24 encerramentos com código zero**, sendo 12 com captura validada e 12 controles sem captura. A segunda incluiu o teste de tarefas: todas as 12 tentativas criaram 25 threads e encontraram o limite. Não houve panic, parada forçada ou OOM local do auxiliar; os artefatos originais permaneceram iguais e os cgroups foram limpos.

Cada linha abaixo tem três execuções. Os valores são mínimo / mediana / máximo do encerramento, em milissegundos:

| Campanha | Meia CPU, controle | Meia CPU, captura | Duas CPUs, controle | Duas CPUs, captura |
| --- | --- | --- | --- | --- |
| Loops no guest | 81,8 / 119,8 / 160,0 | 87,6 / 106,3 / 107,6 | 104,1 / 234,4 / 295,1 | 102,3 / 102,5 / 324,4 |
| Loops + limite de tarefas | 164,8 / 179,0 / 230,0 | 99,5 / 139,2 / 229,3 | 83,0 / 171,7 / 194,0 | 127,3 / 151,7 / 188,7 |

Nas 12 capturas finais, a maior espera completa de uma vCPU **pronta para rodar** foi **13,262 ms**. O maior intervalo da última geração do sinal 35 até sua próxima entrega observada foi **3,842 ms**; até o evento de saída da thread, **3,942 ms**. Cada captura teve exatamente duas gerações, duas entregas e duas saídas de vCPU. Todos os buffers tiveram zero eventos perdidos.

Na campanha sem o teste de tarefas, o contador de throttling da VM não aumentou durante nenhum encerramento. Na campanha com o teste, aumentou uma vez em `half-control-03` e uma vez em `half-trace-03`: 16,301 ms e 9,297 ms de `throttled_usec`, respectivamente. Ambos encerraram normalmente. A maior espera pronta de 13,262 ms pertence à segunda dessas capturas; a coexistência das medidas não permite atribuir toda a espera à cota.

As verificações automatizadas também passaram: **18 testes**, incluindo a rejeição de uma captura só com marcadores e a distinção entre sono e espera pronta. O [controle de concorrência](../evidence/scheduling/lock-check.json) retornou código 2 ao tentar iniciar outra campanha, antes de criar seus contêineres.

O contador `cpu.stat` é do cgroup da VM, não de todos os ancestrais nem do host. Ausência de aumento ali não exclui contenção externa ou throttling em um ancestral. O tempo total inclui a resposta da API e a observação do supervisor; não é o tempo do `join` interno. Com esta amostra pequena e instrumentação diferente, não há base para dizer que a cota de duas CPUs foi sistematicamente mais rápida.

As capturas válidas mostram o sinal **35**, enviado pelo VMM às vCPUs, sua entrega e a saída das threads. Geração e entrega são eventos separados; a cronologia publicada não pressupõe correspondência universal um a um. Não observamos, nessas rodadas, uma vCPU ultrapassando o prazo interno de um segundo. Tampouco coletamos stacks completos do kernel ou os canais em userspace da thread principal. Portanto, não descartamos um problema intermitente em KVM ou no protocolo de encerramento fora desta amostra.

## Tentativas que não entraram na comparação

- `jailer-twtd5stz`: as quatro VMs encerraram com código zero, mas os filtros montados com IDs obtidos por `/proc` e Docker não selecionaram os eventos. Só apareceram os dois marcadores por captura. O relatório inicial dizia sucesso; a revisão marca **falha de captura**. Isso não demonstra ausência de sinais.
- `jailer-_k0n5jq0`: piloto com descoberta corrigida; quatro encerramentos limpos e duas capturas válidas. Não entra nos números das rodadas finais.
- `jailer-bhly3lu2`: vinte encerramentos limpos, mas houve breve sobreposição com o início de outra campanha. A carga mudou; a rodada inteira fica fora da comparação principal.
- `jailer-atefhrbs`: interrompida por esse conflito, com saída 137 e sem relatório completo da suíte. Não é um panic espontâneo da vCPU, nem um resultado aprovado. A tentativa e os hashes dos registros parciais estão preservados.

O wrapper passou a manter um lock por usuário durante campanhas com carga. Ele recusa uma segunda campanha deste wrapper antes de criar contêineres. É uma proteção contra sobreposição entre essas execuções, não um bloqueio de toda carga possível no computador.

## Repetir e revisar

Com Docker/KVM e os artefatos já verificados do lab, crie um perfil explícito fora do repositório a partir de `config/jailer-profile.json`, acrescentando `suite_timeout: 600`, `investigation_matrix: "host-load"` e `diagnostic_repetitions: 3`. Para a segunda campanha, acrescente `diagnostic_pid_probe: true`. Execute **uma campanha por vez**:

```bash
python3 scripts/run_jailer_lab.py \
  --suite vcpu_scheduling --host-load \
  --profile /caminho/scheduling-profile.json \
  --binary-dir /caminho/binarios-oficiais \
  --artifacts /caminho/artefatos \
  --output-parent /caminho/resultados

python3 scripts/export_scheduling_evidence.py \
  --runs /caminho/resultados/jailer-EXECUCAO \
  --output evidence/scheduling

python3 -m unittest discover -s tests -v
```

O resultado local mantém consoles, traces completos, formatos dos tracepoints, perfis, código executado e discos de teste. O GitHub recebe a cronologia revisada dos eventos e seus resumos, hashes dos arquivos brutos, limites e verificações de isolamento. Endereços de memória e identidades de tarefas externas são omitidos da cópia pública; eventos, falhas e tempos das vCPUs permanecem visíveis. Nenhum disco, chave ou dump de memória é publicado.

O próximo diagnóstico precisa capturar uma falha espontânea com esses eventos e, se necessário, acrescentar stacks ou rastreamento do protocolo do VMM. Aumentar o número de encerramentos limpos não substitui essa evidência.

Referências: [instâncias, relógios e buffers do ftrace](https://docs.kernel.org/trace/ftrace.html), [filtros de eventos](https://docs.kernel.org/trace/events.html), [semântica dos eventos de sinais no Linux v6.18](https://github.com/torvalds/linux/blob/v6.18/include/trace/events/signal.h).
