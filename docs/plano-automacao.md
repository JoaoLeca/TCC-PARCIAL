# Plano de automação de atendimento

O objetivo é resolver pelo chat as dúvidas frequentes e a busca de vagas, encaminhar à equipe os casos que precisam de intervenção e levar o mesmo atendimento ao WhatsApp. O site e o Telegram já têm os fluxos locais descritos abaixo. A integração com WhatsApp permanece como etapa futura.

## Ponto de partida

O site oferece atendimento na aba **Conversar** e no painel flutuante, com histórico compartilhado na sessão. A rota `/chat` consulta serviços e disponibilidade com contexto e alternativas mesmo sem chave de IA. O GPT complementa as perguntas abertas e as políticas na base de conhecimento quando `OPENAI_API_KEY` está configurada. A área privada da equipe já oferece fila de atendimento com responsável, contato, observações e resolução.

As reservas são feitas em **Agendar**, onde o cliente pode baixar o comprovante, e pelo fluxo guiado `/agendar` do Telegram. Confirmações repetidas usam a mesma reserva. **Meus horários** consulta as reservas vinculadas à sessão atual sem pedir código. As telas e os comprovantes mostram somente o protocolo e os detalhes da reserva, sem os quatro últimos dígitos do telefone. O chat do site e o Telegram consultam e alteram as reservas da sessão/conversa de origem, com confirmação escrita separada antes de cancelar ou remarcar. As propostas expiram em dez minutos e são revalidadas na gravação. O Telegram oferece `/comprovante` para recuperar a entrega e `/atendente` para contato humano.

Os testes locais usam dados sintéticos. Conforme a decisão de 17/11/2026, as chamadas reais ao modelo e as mensagens reais do Telegram aguardam credenciais no ambiente local. O roteiro de demonstração e avaliação está em [roteiro de automação](../eval/roteiro_automacao.md). As métricas de latência, encaminhamentos e reservas vêm dos eventos do sistema. FCR e acerto das intenções dependem de dados de referência e avaliação efetivamente coletados.

## Contexto de acesso do cliente e validação pendente

Alinhamento da conversa de 18/11/2026:

- O cliente deve conseguir consultar, remarcar e cancelar por conta própria, inclusive em outro dispositivo, sem depender da equipe.
- Na mesma sessão do site, o acesso às reservas é direto. Os quatro últimos dígitos do telefone não são solicitados para autorizar a consulta ou uma alteração.
- A referência visual de quatro dígitos foi removida por decisão do usuário. O protocolo da reserva distingue cada agendamento e não comprova a identidade do cliente.
- Em outra sessão ou canal, a recuperação usa a confirmação do próprio contato no Telegram. No Telegram, o cliente confirma o contato e continua o pedido na conversa. Para recuperar pelo site, recebe no Telegram um código temporário de seis dígitos para informar no site ou chat. Esse código temporário permanece necessário para a recuperação no site; a remoção se refere somente à referência visual de quatro dígitos.
- Remarcações e cancelamentos exigem apresentação dos detalhes e confirmação final separada, mesmo depois da verificação de identidade.

**Ainda não validado com o Telegram real:** o transporte foi simulado nos testes locais. Os 262 testes aprovados e o ensaio no navegador não comprovam a entrega de mensagens em um bot real. É necessário configurar o token, iniciar o bot e executar o roteiro abaixo antes de considerar a integração real validada.

- [ ] Criar uma reserva pelo site em um computador.
- [ ] Em outro dispositivo, abrir a conversa privada do bot e pedir a consulta dessa reserva.
- [ ] Compartilhar o próprio contato, com o mesmo telefone da reserva, e conferir que a reserva correta aparece sem atendimento humano.
- [ ] Pedir uma remarcação por texto, escolher uma vaga e confirmar. Conferir no site e na agenda da equipe que a mesma reserva foi alterada.
- [ ] Pedir um cancelamento por texto e conferir que nada muda antes da confirmação final. Confirmar e verificar que a vaga foi liberada.
- [ ] Reabrir o site em outra sessão e verificar a entrega e a validação do código temporário recebido pelo Telegram.
- [ ] Verificar recusa de contato de outra pessoa, código errado ou expirado e tentativa de acessar uma reserva de outro telefone.

**Decisão confirmada e implementada:** remover os quatro dígitos das telas do cliente e da equipe, do comprovante baixado e do comprovante enviado pelo bot. Manter o protocolo da reserva, o acesso direto na mesma sessão e a recuperação pela verificação do próprio telefone no Telegram. A autenticação e a confirmação final para alterações foram preservadas. O campo técnico de identificação ainda existe na API por compatibilidade, sem ser apresentado ao cliente nem usado como credencial.

**Revisão pelo colega:** seguir o [roteiro de validação real do Telegram](validacao-telegram-real.md). Ainda é necessário configurar e executar o bot real, testar entre dispositivos e registrar o resultado. Não marcar essa validação como concluída apenas porque os testes simulados passaram.

## Onde cada parte funciona

| Local | Responsabilidade |
| --- | --- |
| Site público | Oferecer o chat, apresentar vagas e direcionar para **Agendar** ou **Meus horários** quando necessário. |
| API FastAPI | Interpretar pedidos com o GPT, buscar respostas nas fontes do sistema e validar disponibilidade, identidade e confirmação antes de executar operações. |
| Área privada `/equipe` | Receber encaminhamentos, atribuir responsáveis e acompanhar a resolução de cada atendimento conforme as permissões da equipe. |
| Telegram | Consultar serviços e vagas, confirmar reservas e recuperar comprovantes na conversa privada, compartilhando as regras de agenda e a fila da equipe. |
| WhatsApp Business Platform | Entregar comprovantes e, na etapa seguinte, receber mensagens por webhook e encaminhá-las ao mesmo serviço de atendimento do site. |

## 1. Atendimento e encaminhamento disponíveis para avaliação

- Responder sobre serviços, preços, duração, profissionais, funcionamento e políticas. Consultar o cadastro para dados atuais e usar a base de conhecimento para as políticas.
- Entender pedidos como «Tem corte amanhã com o Rafael?». Perguntar apenas o que faltar e oferecer até três horários reais. Quando não houver vagas, oferecer a busca em outra data ou com outro barbeiro.
- Encaminhar para uma pessoa quando o cliente pedir ou quando o bot não conseguir resolver o caso. Coletar um contato de retorno quando necessário, apresentar o protocolo e disponibilizar um resumo na fila privada com os estados **Aberto**, **Em atendimento** e **Resolvido**.
- Validar com cenários de vaga disponível, dia sem expediente, agenda cheia e atendimento assumido pela equipe. Conferir que os horários retornados respeitam o expediente, a escala e os bloqueios da agenda.

## 2. Enviar comprovantes pelo WhatsApp

- Testar a WhatsApp Cloud API com número de teste e destinatários autorizados. Registrar a autorização do cliente para receber mensagens e usar o modelo aprovado quando exigido pela janela de atendimento do WhatsApp.
- Após confirmar e gravar a reserva, colocar o envio em fila. Vincular o envio à reserva e registrar o identificador da mensagem e seu estado para acompanhar falhas, repetir tentativas com segurança e evitar duplicatas.
- Reutilizar a recuperação já implementada pelo Telegram: contato próprio confirmado, código temporário vinculado à sessão do site e concessão de acesso às reservas daquele telefone. Expandir a entrega para WhatsApp quando o canal estiver configurado. A referência visual de quatro dígitos foi removida; o protocolo permanece no comprovante. Manter o comprovante disponível para download no site, inclusive quando o envio falhar.
- Validar os cenários de entrega, falha e reprocessamento sem duplicação. [Política de mensagens do WhatsApp Business](https://business.whatsapp.com/policy/preview?lang=pt_BR) · [Documentação oficial da Cloud API](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)

## 3. Atender e agendar por conversa

- Receber mensagens do WhatsApp por webhook HTTPS com validação de assinatura e deduplicação. Identificar o canal e a sessão e reutilizar os mesmos serviços da API para consultas e encaminhamento humano.
- Para criar uma reserva por conversa, mostrar serviço, profissional, data, horário e preço. Pedir confirmação explícita, revalidar a vaga na gravação e oferecer novas opções se ela tiver sido ocupada.
- Reutilizar no WhatsApp a camada de autorização por canal e o fluxo de consulta, remarcação e cancelamento já disponível no site e no Telegram. A sessão do site não persiste como conta após o encerramento do Streamlit. Pedidos de outro aparelho/canal e reservas antigas são recuperados automaticamente pelo contato próprio do Telegram, com código temporário no site. O envio real depende do token e do bot em execução. Manter credenciais fora do contexto do modelo e dos logs.
- Depois de validar o atendimento e o agendamento nos dois canais, acrescentar lembretes e avisos de mudança com autorização do cliente.

## Como medir no TCC

Reaproveitar o conjunto de intenções e os cenários em [`eval/`](../eval/). Comparar a resolução no primeiro contato, o tempo de resposta, a proporção de encaminhamentos humanos e a quantidade de reservas concluídas. Separar falhas de entrega no WhatsApp de falhas de atendimento para identificar onde cada etapa precisa melhorar.
