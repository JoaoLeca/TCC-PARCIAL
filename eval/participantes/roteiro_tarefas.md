# Roteiro de tarefas para os participantes

As tarefas descrevem **o que** o participante deve conseguir, sem dizer **como** fazer. Leia cada tarefa em voz alta ou entregue o cartão impresso. Não indique abas, botões nem frases para escrever no chat.

## Cartão de dados fictícios

Entregue um cartão por participante. Use apenas estes dados no sistema.

| Participante | Nome para usar | Telefone para usar |
| --- | --- | --- |
| P01 | Cliente Teste 01 | (16) 90000-0101 |
| P02 | Cliente Teste 02 | (16) 90000-0102 |
| P03 | Cliente Teste 03 | (16) 90000-0103 |
| P04 | Cliente Teste 04 | (16) 90000-0104 |
| P05 | Cliente Teste 05 | (16) 90000-0105 |
| P06 | Cliente Teste 06 | (16) 90000-0106 |
| P07 | Cliente Teste 07 | (16) 90000-0107 |
| P08 | Cliente Teste 08 | (16) 90000-0108 |

Para mais participantes, siga o mesmo padrão.

## Tarefas

As datas devem estar dentro da janela de agendamento da instalação, que vai de 1 a 7 dias à frente. Antes de cada sessão, troque “[dia]” por uma data válida e registre a data usada na ficha.

| ID | Tarefa lida ao participante | Cenários FCR relacionados | Como conferir |
| --- | --- | --- | --- |
| T01 | “Descubra quanto custa fazer cabelo e barba e quanto tempo leva.” | FCR02 | Resposta com R$ 80,00 e 70 min. |
| T02 | “Descubra se a barbearia abre no domingo e em que horário ela funciona.” | FCR05, FCR10 | Segunda a sábado, 8h–12h e 13h30–20h; fechada no domingo. |
| T03 | “Encontre um horário livre para cortar o cabelo em [dia], depois das 15h.” | FCR11 | Horários reais depois das 15h na data pedida. |
| T04 | “Agende um corte de cabelo para [dia], com qualquer barbeiro, usando os dados do seu cartão. Antes de confirmar, confira o que vai ser agendado.” | FCR13, FCR24, FCR30 | Uma reserva nova no painel com o nome do cartão. |
| T05 | “Confira o dia, o horário e o barbeiro da reserva que você acabou de fazer.” | FCR18 | Participante informa os dados corretos da reserva. |
| T06 | “Você não poderá ir nesse horário. Mude a sua reserva para outro dia, à tarde.” | FCR22 | A mesma reserva com nova data no painel; nenhuma reserva duplicada. |
| T07 | “Agora cancele a sua reserva.” | FCR19 | Reserva cancelada no painel. |
| T08 | “Tente marcar um corte com o Rafael em uma segunda-feira. Se não for possível, encontre outra opção.” | FCR15 | O sistema informa a folga e oferece alternativa real. |
| T09 | “Você quer fazer uma reclamação. Peça para alguém da barbearia entrar em contato com você.” | FCR27 | Chamado aberto na fila da equipe com o telefone do cartão. |
| T10 | “Descubra quais formas de pagamento a barbearia aceita e qual é a tolerância para atraso.” | FCR04, FCR06 | Dinheiro, Pix e cartão; 15 minutos de tolerância. |

Depois da T09, pergunte: “O que vai acontecer agora com o seu pedido?”. Anote se o participante entendeu que o retorno será por telefone e que o chat continua sendo um assistente virtual.

## Ordem de aplicação

Alterne a ordem entre os participantes para diminuir o efeito de aprendizado. As tarefas T04 a T07 dependem umas das outras e ficam sempre juntas, nessa ordem.

| Versão | Ordem |
| --- | --- |
| A | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10 |
| B | T08, T09, T10, T01, T04, T05, T06, T07, T02, T03 |
| C | T03, T10, T04, T05, T06, T07, T09, T01, T08, T02 |

Distribua as versões em sequência: P01 → A, P02 → B, P03 → C, P04 → A e assim por diante.

## Protocolo do observador

- Leia a tarefa e anote a hora de início. Não sugira caminhos, botões ou frases.
- Se o participante pedir ajuda, responda primeiro: “Faça como faria em casa”. Se ele continuar parado por cerca de 2 minutos, ou pedir ajuda pela segunda vez, dê a menor dica possível e registre o momento e o motivo em `motivo_ajuda`.
- Encerre a tarefa quando o participante disser que terminou ou desistir, ou depois de 5 minutos sem progresso. Anote a hora de fim.
- Em `resultado`, use `independente` (concluiu sem ajuda), `com_ajuda` (concluiu após dica) ou `nao_concluida`.
- Em `erros`, conte as tentativas que levaram a um caminho errado ou a uma mensagem de erro. Em `repeticoes`, conte as vezes em que o sistema pediu de novo uma informação que o participante já tinha dado.
- Confira no painel ou no banco o efeito das tarefas T04, T06, T07 e T09 antes de marcar como concluídas.
- Não anote nome, telefone real ou outros dados pessoais. Use somente o código do participante.
