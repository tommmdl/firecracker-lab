# Próximos exercícios

[English](roadmap.en.md)

O boot agora tem um controlador no repositório, regressões para a leitura do console e evidências de execuções locais. O resultado antigo continua registrado como falha da automação.

1. Preparar kernel/rootfs a partir de fontes verificadas e versões fixadas, para reproduzir também a montagem do ambiente.
2. Inspecionar `fcnet.service`, o login automático e a configuração SSH antes de acrescentar rede.
3. Escolher uma sub-rede sem conflito e criar uma TAP exclusiva, inicialmente só para host/guest.
4. Validar SSH por chave e a identidade do host, com `known_hosts` exclusivo.
5. Implementar NAT e regras de saída limitadas ao lab, testando destinos permitidos e negados e a reversão.
6. Executar uma tarefa conhecida em um disco descartável e recolher código de saída, logs e resultados.
7. Validar `jailer`, limites de CPU/memória/tempo e o comportamento na falha antes de avançar para tarefas não confiáveis.

Rede, SSH, firewall e `jailer` continuam pendentes. Esta etapa não altera esses componentes nem publica o repositório.
