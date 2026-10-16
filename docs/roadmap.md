# Pr?ximas etapas

1. Corrigir a interpreta??o do console e registrar separadamente resultado da automa??o e evid?ncias do guest.
2. Inspecionar `fcnet.service`, configura??o SSH e presen?a da chave p?blica no guest, sem divulgar chaves privadas.
3. Escolher uma sub-rede sem conflito e criar TAP exclusiva, inicialmente sem encaminhamento externo.
4. Configurar endere?os host/guest e validar somente a comunica??o entre eles.
5. Validar SSH por chave, verificar a identidade do host e manter `known_hosts` exclusivo.
6. Acrescentar encaminhamento e NAT com regras limitadas ao laborat?rio e rollback documentado.
7. Aplicar bloqueio de egress por padr?o e libera??es espec?ficas; testar destinos permitidos e negados e conferir contadores no host.
8. Evoluir para workloads de agentes com c?pias descart?veis, limites de CPU/mem?ria/tempo, jailer, credenciais tempor?rias, proxy de sa?da e coleta de resultados.

As etapas de rede e firewall n?o foram implementadas. Cada mudan?a dever? ter autoriza??o, diagn?stico pr?vio, valida??o e revers?o. O boot validado isoladamente n?o comprova isolamento adequado para c?digo n?o confi?vel.
