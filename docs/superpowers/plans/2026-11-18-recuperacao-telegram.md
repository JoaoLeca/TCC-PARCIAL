# Recuperação de reservas pelo Telegram

> Execução no chat atual com testes antes da implementação e revisão independente ao concluir.

**Objetivo:** permitir consultar e alterar reservas em outra sessão ou canal sem atendimento da equipe.

**Decisão do usuário:** usar o Telegram para a verificação. A decisão posterior removeu os quatro últimos dígitos das telas e dos comprovantes; permanece o protocolo da reserva. A alteração da reserva sempre exige confirmação final separada.

**Arquitetura:** o site abre um desafio ligado ao segredo da sessão e ao telefone informado. Um link abre a conversa privada do bot. O bot exige o contato do próprio remetente e compara o telefone completo antes de entregar um código aleatório de seis dígitos. A validação no site concede acesso às reservas daquele telefone, incluindo reservas antigas, sem substituir o proprietário original. No Telegram, o próprio contato confirmado concede acesso diretamente.

**Limites:** desafio de dez minutos, código utilizável por cinco minutos, cinco tentativas, limite de novas solicitações por sessão e limite de emissões por telefone após contato confirmado, permissões recuperadas por 24 horas. O código e o identificador na tabela de desafios são armazenados como hash. Links temporários permanecem somente no estado necessário do fluxo e são retirados do histórico enviado à IA. Telefone informado por texto e quatro dígitos nunca concedem acesso. Falta de token do bot é informada sem simular envio. Contato de Telegram compartilhado ou encaminhado de outra pessoa é recusado.

## Etapas

- [x] Criar regressões para recuperação de reservas antigas, telefone com +55, proprietários diferentes, zeros à esquerda, isolamento entre sessões, código inválido, expiração, reutilização, concorrência e limites de tentativas.
- [x] Implementar `customer_recovery.py`, tabelas de desafios e concessões e leitura de concessões em `customer_access.py`.
- [x] Integrar rotas privadas de recuperação, pedidos por chat e coleta de código fora das mensagens enviadas ao modelo.
- [x] Integrar contato próprio e links de recuperação no bot. Preservar os pedidos por texto e a confirmação final.
- [x] Adicionar recuperação em Meus horários, retirar o encaminhamento obrigatório à equipe e atualizar documentação.
- [x] Executar testes, ensaio isolado no navegador e revisão. Reiniciar a API após backup. Não enviar mensagens reais sem credenciais configuradas.

Validação: 262 testes aprovados e ensaio isolado no navegador com transporte Telegram simulado. A revisão final não deixou problemas importantes pendentes. API local reiniciada após backup do banco. O envio real aguarda `TELEGRAM_BOT_TOKEN` e o bot em execução.

Atualização posterior confirmada pelo usuário: referência visual de quatro dígitos removida, mantendo autenticação e recuperação pelo Telegram. A validação com mensagens reais continua pendente. O colega responsável pela revisão deve seguir `docs/validacao-telegram-real.md`.
