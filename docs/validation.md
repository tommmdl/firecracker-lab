# Valida??o funcional

## Evid?ncias observadas em 16/10/2026

O console do guest registrou:

```text
Ubuntu 24.04.5 LTS ubuntu-fc-uvm ttyS0
root (automatic login)
uid=0(root) gid=0(root) groups=0(root)
6.18.54+
/dev/vda ext4 rw,relatime
```

A API recebeu `SendCtrlAltDel` e respondeu HTTP 204. O guest executou a sequ?ncia de shutdown e o Firecracker registrou `exiting successfully. exit_code=0`.

N?o houve valida??o de senha: o login foi autom?tico. O marcador final n?o foi reconhecido pelo controlador, que registrou falha de automa??o apesar das evid?ncias presentes no console. N?o se deve declarar sucesso autom?tico at? corrigir esse reconhecimento.

## Verifica??es estruturais anteriores

- `e2fsck -f -n` no ext4 terminou com c?digo 0, sem reparos.
- `gzip -t` e listagem TAR terminaram com c?digo 0.
- Os 23 arquivos presentes no manifesto SHA256 local da distribui??o conferiram.
- Os bin?rios de trabalho coincidiram com os bin?rios da distribui??o.
- O kernel possui cabe?alho ELF x86-64 leg?vel.
- O SquashFS possui superbloco v?lido; seu conte?do integral n?o foi validado.

O manifesto local comprova consist?ncia local, sem autentica??o independente da origem.

## Procedimento para um pr?ximo teste autorizado

1. Confirmar acesso ao KVM, mem?ria, espa?o livre e aus?ncia de processos ou usu?rios do disco.
2. Calcular SHA256 do disco e da configura??o originais.
3. Criar diret?rio exclusivo sem sobrescrita; copiar o disco e comparar os hashes.
4. Criar configura??o separada apontando para o kernel existente e o disco clonado.
5. Iniciar uma ?nica VM, sem rede, com socket exclusivo e registro de console no diret?rio novo.
6. Normalizar CR/LF ao interpretar o console e reconhecer login autom?tico sem enviar um nome de usu?rio desnecess?rio.
7. Confirmar kernel, identidade do guest e montagem do rootfs por comandos no console.
8. Solicitar reboot pelo guest ou Ctrl+Alt+Del pela API; confirmar a sa?da do processo.
9. Recalcular os hashes originais e comparar com os valores anteriores.

Forma do comando (substitua os caminhos):

```bash
/caminho/firecracker \
  --api-sock /diretorio-exclusivo/api.socket \
  --config-file /diretorio-exclusivo/vm-config.json
```

A exist?ncia do processo ou do socket n?o comprova boot. Falhas exigem interrup??o, registro e diagn?stico; n?o repetir testes ou avan?ar para rede automaticamente.
