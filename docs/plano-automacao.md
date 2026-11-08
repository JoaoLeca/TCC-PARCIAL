# Plano de automação de atendimento

O objetivo é resolver pelo chat as dúvidas frequentes e a busca de vagas, encaminhar à equipe os casos que precisam de intervenção e levar o mesmo atendimento ao WhatsApp. As etapas abaixo descrevem trabalho futuro.

## Ponto de partida

O site oferece atendimento na aba **Conversar** e no painel flutuante, com histórico compartilhado na sessão. A rota `/chat` do FastAPI usa GPT para interpretar mensagens, consulta o catálogo e a agenda para obter dados atuais e busca as políticas na base de conhecimento. O GPT requer `OPENAI_API_KEY`. A API registra solicitações de atendimento humano, e a equipe ainda precisa de uma fila para assumir e acompanhar esses casos.

As reservas são feitas em **Agendar**, onde o cliente pode baixar o comprovante. **Meus horários** exige o telefone e o código privado da reserva para consultar, remarcar ou cancelar.

## Onde cada parte funciona

| Local | Responsabilidade |
| --- | --- |
| Site público | Oferecer o chat, apresentar vagas e direcionar para **Agendar** ou **Meus horários** quando necessário. |
| API FastAPI | Interpretar pedidos com o GPT, buscar respostas nas fontes do sistema e validar disponibilidade, identidade e confirmação antes de executar operações. |
| Área privada `/equipe` | Receber encaminhamentos, atribuir responsáveis e acompanhar a resolução de cada atendimento conforme as permissões da equipe. |
| WhatsApp Business Platform | Entregar comprovantes e, na etapa seguinte, receber mensagens por webhook e encaminhá-las ao mesmo serviço de atendimento do site. |

## 1. Validar atendimento e encaminhamento no site

- Responder sobre serviços, preços, duração, profissionais, funcionamento e políticas. Consultar o cadastro para dados atuais e usar a base de conhecimento para as políticas.
- Entender pedidos como «Tem corte amanhã com o Rafael?». Perguntar apenas o que faltar e oferecer até três horários reais. Quando não houver vagas, oferecer a busca em outra data ou com outro barbeiro.
- Encaminhar para uma pessoa quando o cliente pedir ou quando o bot não conseguir resolver o caso. Coletar um contato de retorno quando necessário, apresentar o protocolo e disponibilizar um resumo na fila privada com os estados **Aberto**, **Em atendimento** e **Resolvido**.
- Validar com cenários de vaga disponível, dia sem expediente, agenda cheia e atendimento assumido pela equipe. Conferir que os horários retornados respeitam o expediente, a escala e os bloqueios da agenda.

## 2. Enviar comprovantes pelo WhatsApp

- Testar a WhatsApp Cloud API com número de teste e destinatários autorizados. Registrar a autorização do cliente para receber mensagens e usar o modelo aprovado quando exigido pela janela de atendimento do WhatsApp.
- Após confirmar e gravar a reserva, colocar o envio em fila. Vincular o envio à reserva e registrar o identificador da mensagem e seu estado para acompanhar falhas, repetir tentativas com segurança e evitar duplicatas.
- Definir uma forma segura de entregar ou recuperar o código privado antes de usá-lo em mensagens. Manter o comprovante disponível para download no site, inclusive quando o envio falhar.
- Validar os cenários de entrega, falha e reprocessamento sem duplicação. [Política de mensagens do WhatsApp Business](https://business.whatsapp.com/policy/preview?lang=pt_BR) · [Documentação oficial da Cloud API](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)

## 3. Atender e agendar por conversa

- Receber mensagens do WhatsApp por webhook HTTPS com validação de assinatura e deduplicação. Identificar o canal e a sessão e reutilizar os mesmos serviços da API para consultas e encaminhamento humano.
- Para criar uma reserva por conversa, mostrar serviço, profissional, data, horário e preço. Pedir confirmação explícita, revalidar a vaga na gravação e oferecer novas opções se ela tiver sido ocupada.
- Direcionar a consulta, a remarcação e o cancelamento para **Meus horários** até haver uma verificação segura equivalente no canal. Manter o código privado fora das mensagens enviadas ao modelo e dos logs.
- Depois de validar o atendimento e o agendamento nos dois canais, acrescentar lembretes e avisos de mudança com autorização do cliente.

## Como medir no TCC

Reaproveitar o conjunto de intenções e os cenários em [`eval/`](../eval/). Comparar a resolução no primeiro contato, o tempo de resposta, a proporção de encaminhamentos humanos e a quantidade de reservas concluídas. Separar falhas de entrega no WhatsApp de falhas de atendimento para identificar onde cada etapa precisa melhorar.
