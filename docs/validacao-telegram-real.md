# Roteiro simples de validação

## O que já foi feito

Os quatro dígitos do telefone foram retirados das telas e dos comprovantes. Permanecem o protocolo e os detalhes da reserva. Na mesma sessão do site, o cliente acessa suas reservas diretamente. Em outra sessão ou no Telegram, recupera o acesso confirmando o próprio telefone. Cancelar e remarcar continuam exigindo confirmação final.

**Situação:** os testes locais passaram. A entrega de mensagens por um bot real ainda precisa ser validada pelo colega responsável pela revisão.

## Para testar agora, sem token externo

A API local e o site precisam estar ligados. Não é necessário contratar ou configurar uma API externa.

1. Agendar pelo site e conferir o resumo e o comprovante baixado. Os quatro dígitos não devem aparecer como código.
2. Sem fechar ou recarregar a página, abrir **Meus horários** e consultar a reserva.
3. Remarcar pela tela e conferir a nova data. Pedir cancelamento e conferir que só acontece depois de confirmar.
4. Fazer outra reserva e, no chat do site, escrever “quero ver meu agendamento”, “quero remarcar” ou “quero cancelar”. Conferir o resumo e a confirmação final.
5. Testar serviços, preços e busca de vagas no chat, por exemplo “quero corte amanhã depois das 15h”. Esses pedidos guiados funcionam sem IA externa.
6. Na área da equipe, conferir a agenda, os bloqueios e as reservas conforme as permissões da conta.

**Atenção:** a sessão pode terminar ao fechar ou recarregar a página. A recuperação em uma sessão nova depende do Telegram configurado.

## O que depende do token do Telegram

Preparar: preencher `TELEGRAM_BOT_TOKEN` apenas no `.env` local e iniciar o bot conforme o [README](../README.md#bot-do-telegram). Usar uma conta Telegram com o mesmo telefone da reserva.

- [ ] Agendar no site e, pelo celular, pedir a consulta no bot. Compartilhar o próprio contato e conferir que a reserva aparece sem ajuda da equipe.
- [ ] Pedir uma remarcação por mensagem, escolher uma vaga e confirmar. Conferir a atualização no site e na agenda da equipe.
- [ ] Pedir cancelamento e responder “não”. A reserva deve continuar ativa. Repetir o pedido e responder “sim”. A reserva deve ser cancelada.
- [ ] Abrir o site em uma sessão nova. Em **Meus horários**, informar o telefone, abrir o bot e confirmar o contato. Receber o código temporário e digitá-lo no site para recuperar a reserva.
- [ ] Conferir que contato de outra pessoa, código errado, expirado ou reutilizado não liberam acesso.

O código temporário de seis dígitos continua existindo para a recuperação no site. Ele é diferente da referência visual de quatro dígitos que foi removida.

## O que depende da chave da OpenAI

Respostas abertas geradas pela IA e consultas às políticas com busca na base de conhecimento. Os fluxos guiados de agenda e as regras de confirmação funcionam sem essa chave. A chave da OpenAI não substitui o token do Telegram.

## Registro do colega

- Responsável e data:
- Código/revisão testada:
- Passos que funcionaram:
- Problemas encontrados:
- Resultado do teste real do Telegram: pendente / aprovado / precisa corrigir.

Não publicar token, código temporário ou dados pessoais nas evidências. Manter a validação do Telegram real como **pendente** até executar e registrar os testes acima.
