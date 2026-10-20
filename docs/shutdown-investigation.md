# Por que o shutdown terminou com SIGSEGV?

[English](shutdown-investigation.en.md) · [README](../README.md) · [Resultados revisados](../evidence/shutdown-investigation.json)

A etapa seguinte usou tracefs sem pausas de depurador: [escalonamento e sinais sob carga](vcpu-scheduling.md). As duas campanhas finais registraram 24 encerramentos limpos e 12 capturas válidas, incluindo o teste de tarefas. A maior espera pronta foi 13,262 ms; nenhuma reproduziu o panic espontâneo. A investigação preserva uma captura vazia inicialmente aprovada por engano e uma sobreposição de campanhas excluída da comparação.

O caso original teve boot válido, remount do disco como somente leitura e um panic ao esperar a saída da vCPU 0. Depois, o processo terminou com SIGSEGV. Investigamos essas duas etapas separadamente. A sequência do SIGSEGV foi reproduzida com instrumentação; o motivo exato do atraso **na execução histórica** continua em aberto.

## O que o código e os testes mostraram

No Firecracker v1.17.0, o encerramento envia `Finish` às vCPUs, solicita saída de KVM e aguarda a desconexão do canal de resposta de cada thread. O prazo interno é de **um segundo**; ele é diferente do prazo externo de 15 segundos do supervisor. Se uma thread não concluir nesse intervalo, o VMM entra em panic.

O panic não identifica o motivo do atraso. Uma vCPU pode não ter recebido tempo de CPU, estar presa num caminho do kernel ou não concluir uma etapa do protocolo. O console histórico não tem stack, estado por thread ou captura de sinais que permita escolher entre essas hipóteses.

No teste controlado, o supervisor usou `ptrace` para pausar uma única vCPU na mensagem de teardown, mantendo a thread principal em execução. Isso provocou o mesmo panic. O rastreamento mostrou duas tentativas de SIGABRT, seguidas de SIGSEGV na instrução `hlt`, dentro de `a_crash` da musl. O endereço relativo `0x26cae6` do binário oficial foi conferido com seus símbolos de debug e a desmontagem do executável.

O Firecracker roda como **PID 1** no namespace criado pelo jailer. Esse PID tem tratamento especial de sinais. No caminho observado, as tentativas de aborto não encerraram o processo; a musl chegou à instrução de emergência. Portanto, o SIGSEGV desse controle é um efeito posterior ao panic, não evidência de um acesso acidental a um ponteiro do guest. A execução antiga não foi rastreada: atribuir exatamente a mesma sequência a ela continua sendo uma inferência compatível com seus logs, versão e perfil.

Não removemos o namespace de PID para mudar o código de saída, nem desativamos seccomp para fazer o teste passar.

## Quota de CPU: hipótese testada, sem conclusão fácil

As matrizes registram cada execução, inclusive os resultados que enfraquecem a hipótese inicial:

- Encerramentos com meia CPU e os dois loops no guest terminaram normalmente nas repetições. O comando histórico de parada dos loops, sem confirmação de `wait`, foi mantido nesses controles. Eles não repetem toda a combinação antiga com o teste de tarefas.
- Um controle reduziu o teto para apenas 1 ms de CPU por segundo no início do teardown. O encerramento observado levou cerca de **10,28 s**, com forte throttling, mas saiu com código **0**.
- No par com o mesmo aperto artificial, restaurar o teto limitado de duas CPUs após cerca de 250 ms permitiu concluir em aproximadamente **0,63 s**, também com código **0**.

A falta de CPU pode atrasar o encerramento, mas esses resultados **não demonstram que ela causou o panic antigo**. O prazo de um segundo começa na espera do canal; um atraso total de dez segundos não significa que aquela espera específica tenha excedido um segundo. O rastreador também acrescenta pausas e altera o escalonamento. Os tempos servem à comparação local, não são um benchmark.

## Correção candidata: tolerar um atraso curto, continuar limitado

Compilamos duas variantes a partir do mesmo commit do Firecracker, com o mesmo toolchain e `Cargo.lock`: a original e uma candidata cuja única alteração no fonte é aumentar `VCPU_JOIN_TIMEOUT` de um para cinco segundos. O [construtor](../scripts/build_shutdown_candidate.py) fixa commit, checksum do arquivo de fontes e digest da imagem base do build. O Dockerfile e o ID da imagem derivada também ficam registrados. Os pacotes APT resolvidos têm versões no log; os repositórios APT não são um snapshot fixado, portanto não prometemos reconstrução futura bit a bit. A origem é HTTPS do GitHub; o checksum fixado não acrescenta uma assinatura independente do mantenedor.

A original usada no A/B é recompilada, não é o mesmo arquivo da distribuição oficial usado na análise inicial. As duas variantes do A/B compartilham o mesmo fonte da política seccomp e o mesmo filtro BPF gerado, com hashes registrados. Um fallback para filtro vazio reprova o build.

A candidata é um experimento local. Não substitui silenciosamente o binário oficial e não é uma correção upstream. Aumentar o prazo reduz a sensibilidade a um atraso transitório; não conserta um deadlock, um evento perdido ou um problema de KVM. O controle longo verifica que uma thread que continua impedida de terminar ainda produz falha.

Os perfis explícitos e os resultados do par ficam nas evidências. UID/GID, capacidades de execução zeradas, `NoNewPrivs`, seccomp e os namespaces do jailer são conferidos após o boot de cada cenário. Os limites de memória, tarefas e descritores permanecem ativos. Os tetos de CPU continuam finitos. As falhas provocadas continuam sendo falhas do processo, mesmo quando o experimento confirma o comportamento esperado.

## Validar o executável antes de iniciar uma VM

Uma compilação anterior terminou com sucesso, mas os dois executáveis continham `PT_INTERP` apontando para `/lib/ld-musl-x86_64.so.1`. Não havia `DT_NEEDED`; ainda assim, o interpretador tornava o arquivo inadequado para a jail mínima. Essa etapa foi reprovada na validação do artefato, antes de qualquer teste com essa candidata.

O driver `musl-gcc` usado na ligação final acrescentava o interpretador. O construtor passou a usar `cc` nessa etapa, mantendo o target `x86_64-unknown-linux-musl` e o compilador musl para dependências C. Primeiro ele compila e executa um programa mínimo estático; depois aplica as mesmas checagens ao Firecracker original e à candidata. Exige ausência de `PT_INTERP` e `DT_NEEDED`, execução de `--version` e hash do artefato. O wrapper repete a checagem estática e o hash antes de iniciar o gerador de carga ou o auxiliar das VMs.

O controle negativo usou o executável rejeitado e recebeu código 2, sem iniciar contêineres de teste. Também registramos uma referência local de imagem digitada incorretamente, que retornou código 125. O construtor lê o ID completo do arquivo gerado pelo Docker e confere sua resolução com `docker image inspect`; IDs não são completados manualmente.

## Duas falhas da automação encontradas sob carga

Na primeira campanha com os binários estáticos, a sétima repetição da versão original recebeu HTTP 204, mas não iniciou o reboot. O registro do teclado apareceu depois do pedido; o VMM continuou no loop de eventos e as vCPUs em KVM. A quota não acumulou novos períodos de throttling durante aqueles 15 segundos. O supervisor encerrou a cópia de teste com SIGKILL. Isso ocorreu **antes do teardown**, sem o panic de join, e não é corrigido aumentando o prazo de um para cinco segundos.

A mesma corrida de prontidão apareceu no controle curto da candidata. O shell estar disponível não prova que o teclado já tenha um handler ativo. A correção consulta `/proc/bus/input/devices` dentro do guest e aguarda o dispositivo `AT Raw Set 2 keyboard` com handler `kbd`. A resposta é delimitada por um nonce, para não aceitar o comando ecoado no console. Só então é enviado o pedido de shutdown; não repetimos automaticamente um pedido perdido para transformar a tentativa antiga em sucesso.

Nos controles da versão original, o rastreador também capturou o panic e SIGSEGV, mas não confirmou o término do processo. Esses resultados continuam reprovados, com código final desconhecido quando a coleta ficou incompleta. A revisão libera as vCPUs rastreadas antes de desanexar a thread principal, recolhe seus estados de espera e deixa a instrução `hlt` gerar a falha novamente após desanexar. O sinal observado e o código final recolhido são dados separados.

A campanha anterior continua inteira nas evidências. A nova comparação usa os mesmos binários e limites, com a checagem de prontidão e a coleta revisadas para ambas as variantes.

## Repetições com carga no host

O gerador de carga roda em outro contêiner, sem privilégios, sem capacidades, sem rede e como UID/GID 65534. Dois processos disputam as mesmas CPUs do host WSL/KVM usadas pelo auxiliar das VMs: CPUs 6 e 7 neste ambiente de oito CPUs disponíveis. O gerador tem teto de duas CPUs, 64 MiB e oito tarefas, verifica se seus workers continuam vivos e é removido ao fim. Seu prazo máximo é de 600 segundos.

Essa é pressão de CPU em duas CPUs selecionadas do WSL, não um teste de memória, I/O ou do escalonador Windows. Os relatórios registram afinidade, limites, saúde do gerador e pressão de CPU. As repetições sem injeção alternam guest ocioso e dois loops de CPU antes do shutdown. Nos casos com carga, também repetem o teste confiável de criação de threads no mesmo cgroup, seguido de nova conferência das proteções; os controles de thread pausada são apresentados separadamente.

| Variante recompilada, coleta corrigida | Encerramentos limpos sem falha induzida | Falhas inesperadas | Tempo mínimo / mediana / máximo |
| --- | --- | --- | --- |
| Original, espera de 1 s | 10/10 | 0 | 0,107 / 0,197 / 0,376 s |
| Candidata, espera de 5 s | 10/10 | 0 | 0,110 / 0,197 / 0,256 s |

| Controle sob a mesma carga no host | Original de 1 s | Candidata de 5 s |
| --- | --- | --- |
| Pausa planejada de 1,5 s numa vCPU | Panic após cerca de 1,06 s; término recolhido: SIGSEGV (`-11`) | Thread liberada; shutdown limpo (`0`) |
| Pausa planejada de 6 s numa vCPU | Panic após cerca de 1,06 s; SIGSEGV (`-11`) | Panic após cerca de 5,01 s; SIGSEGV (`-11`) |

Os tempos dos panics são medidos desde a pausa injetada, não desde o início do boot. As VMs que falham terminam antes de completar a pausa planejada. A matriz confirma o resultado esperado, mas **as três VMs com panic continuam falhas de processo**, não encerramentos limpos.

[Original, rodada corrigida](../evidence/shutdown-runs/jailer-koveu1da.json) · [Candidata, rodada corrigida](../evidence/shutdown-runs/jailer-qc_wk0g4.json).

Antes da correção da automação, a [original teve 9/10 encerramentos limpos e dois controles inconclusivos](../evidence/shutdown-runs/jailer-w3_psdae.json); a [candidata teve 10/10 limpos, mas seu controle curto falhou antes do teardown](../evidence/shutdown-runs/jailer-z3dqb0mq.json). As duas campanhas anteriores continuam reprovadas. Não misturamos suas contagens com a comparação corrigida.

Todas as 24 VMs da comparação corrigida tiveram prontidão do teclado confirmada e runtime aprovado pela política observada do jailer. Os geradores de carga estavam vivos ao início e ao fim, sem OOM; seus snapshots finais mostraram cerca de 200% de CPU, medidos depois da matriz. As cópias originais de entrada mantiveram os hashes e os cgroups foram removidos. Os 15 testes de regressão também passaram.

A candidata tolerou o atraso curto controlado e preservou um limite para o atraso longo. **Não comprovamos a causa do atraso espontâneo da execução histórica nem sua eliminação.** Dez repetições por variante, em um único host, não excluem uma falha rara. A candidata continua experimental; o perfil padrão usa o binário oficial.


## Como repetir

Use os artefatos conhecidos do [build de kernel e rootfs](build-from-source.md), Docker/KVM e o par revisado Firecracker/jailer v1.17.0. O auxiliar de execução continua **privilegiado e confiável**, sem rede, limitado a duas CPUs, 768 MiB e 256 tarefas; ele não é uma fronteira segura para operadores não confiáveis.

```bash
python3 scripts/run_jailer_lab.py --suite shutdown_investigation \
  --binary-dir "$HOME/fc-lab" \
  --artifacts "$HOME/fc-build/run-01/artifacts" \
  --output-parent "$HOME/fc-lab"

python3 scripts/build_shutdown_candidate.py \
  --output "$HOME/fc-shutdown-build-new" \
  --jailer "$HOME/fc-lab/jailer"
```

A saída do build precisa ser um diretório novo. O build usa um contêiner comum, sem privilégios, com rede para obter dependências fixadas no lockfile, teto de duas CPUs, 3 GiB e 256 tarefas. Os binários e perfis explícitos ficam em `stock/` e `candidate/`, com hashes no relatório. Os perfis gerados selecionam dez repetições e os dois controles, com prazo externo de até 600 segundos para a matriz inteira.

Para testar uma variante sob carga no host:

```bash
python3 scripts/run_jailer_lab.py --suite shutdown_investigation --host-load \
  --profile "$HOME/fc-shutdown-build-new/candidate/profile.json" \
  --binary-dir "$HOME/fc-shutdown-build-new/candidate" \
  --artifacts "$HOME/fc-build/run-01/artifacts" \
  --output-parent "$HOME/fc-lab"
```

Repita com `stock` no lugar de `candidate` para o controle original. A opção `--reuse-from` do construtor pode aproveitar um diretório anterior conhecido, que fica registrado como cache reutilizado; ela não é necessária à receita limpa. Não substitua o perfil normal por um perfil de diagnóstico.

O rastreador é específico de Linux x86_64. O wrapper conserva snapshot dos scripts/perfil, comandos de lançamento, relatórios por cenário, consoles e hashes. Cada guest usa uma cópia nova do disco. As entradas são montadas somente para leitura e seus hashes são comparados antes/depois; não há migração nem alteração dos discos originais.

## O que deu errado durante a investigação

A primeira versão do rastreador continuava entregando SIGSEGV sob `ptrace`, repetindo a parada na mesma instrução até o prazo externo. O cleanup matou essa VM com SIGKILL. Esse ensaio está **reprovado**, preservado separadamente; não é um encerramento natural nem substitui o SIGSEGV antigo. A versão seguinte registra a primeira falha, desanexa o rastreador e confirma o término real com SIGSEGV.

O primeiro build também foi interrompido: a imagem tinha Cargo, mas não o executável Git solicitado pela configuração do projeto. O script passou a usar o transporte Git interno do Cargo. O código 137 daquela interrupção administrativa não é apresentado como OOM ou teste aprovado. A tentativa seguinte não tinha libclang. Depois de instalá-la, o compilador de host musl ainda não conseguia carregar a biblioteca dinâmica usada pelo bindgen. O construtor final usa host GNU e destino musl estático. Também faltava expor os headers Linux ao musl-gcc e, depois, a biblioteca de desenvolvimento libseccomp para gerar os filtros. O layout dos headers foi alinhado ao contêiner de desenvolvimento upstream. O aviso de rustfmt ausente era não fatal e também foi resolvido. Todas essas tentativas de build continuam reprovadas nos registros.

Continuam pendentes a captura espontânea do atraso histórico, comparação em outro host/kernel e revisão da candidata antes de qualquer adoção. Nenhum desses resultados certifica isolamento para código arbitrário não confiável. As demais pendências de CPU, rede, bootstrap privilegiado e `CapBnd` estão no [guia do jailer](jailer.md).

## Fontes usadas na análise

- [Espera da vCPU, eventos e kick em v1.17.0](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/vstate/vcpu.rs)
- [Envio de Finish durante o teardown](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/vstate/vm.rs)
- [Hook de panic](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/firecracker/src/main.rs) e [tratadores de sinais](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/src/vmm/src/signal_handler.rs)
- [Ambiente de build upstream](https://github.com/firecracker-microvm/firecracker/blob/v1.17.0/tools/devctr/Dockerfile)
- [abort da musl](https://git.musl-libc.org/cgit/musl/tree/src/exit/abort.c) e [regras do PID 1](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
