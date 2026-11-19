# Ensaio de aceitação no site – 18/11/2026

> Ensaio executado pelos desenvolvedores com automação do navegador e dados fictícios. **Não** é avaliação com participantes e não substitui a avaliação humana de resolução descrita em [`eval/roteiro_automacao.md`](../../roteiro_automacao.md).

## Configuração

- **Código avaliado:** commit `f78bc9b` da `main`.
- **Data e hora:** 18/11/2026, das 22:22 às 22:25 (America/Sao_Paulo).
- **Canal:** site (web). O Telegram não foi avaliado, pois não havia `TELEGRAM_BOT_TOKEN` configurado.
- **IA:** desativada (sem `OPENAI_API_KEY`); todas as respostas vieram dos fluxos determinísticos.
- **Janela de reservas:** 1 a 7 dias (19/11 a 25/11/2026). Datas usadas: 19/11 e 20/11 (reservas), 24/11 (restrições de horário e remarcação), 25/11 (sem vagas) e 21/11 (último horário de sábado).
- **Banco:** SQLite de avaliação criado do zero, apenas com a conta de dono; clientes e telefones fictícios.
- **Execução:** [`eval/aceitacao_site.mjs`](../../aceitacao_site.mjs) controla o Chrome sem interface pelo DevTools Protocol, consulta o banco antes e depois de cada passo e usa a API da equipe para criar bloqueios e alterar o preço do corte. Nos cenários A06 e A07, as mensagens foram enviadas diretamente à rota `/chat`, a mesma usada pelo site.

## Resumo

| Cenário | Situação | Verificações aprovadas |
| --- | --- | --- |
| A05 – IA desativada | **Reprovado** | 7 de 9 |
| A06 – Restrições de horário sem IA | **Aprovado** | 9 de 9 |
| A07 – Mudança de contexto temporal | **Aprovado** | 8 de 8 |
| A02 – Sem vagas e alternativas | **Aprovado** | 9 de 9 |
| A03 – Solicitação de retorno da equipe | **Aprovado** | 8 de 8 |
| A01 – Reserva confirmada (site) | **Aprovado** | 5 de 5 |
| A08 – Sugestão clicável e confirmação | **Aprovado** | 13 de 13 |
| A09 – Consulta e alteração de reservas por mensagem | **Aprovado** | 11 de 11 |
| A10 – Isolamento entre sessões e recuperação sem o bot | **Aprovado** | 6 de 6 |
| A04 – Botões antigos do Telegram | **Não executado** | – |

## Defeito encontrado no A05

Na mesma conversa, depois de uma consulta de vagas (“Tem horário para corte dia 24/11?”), a pergunta fora do escopo “Qual a previsão do tempo para amanhã?” recebeu horários de corte para o dia seguinte, em vez da mensagem de IA indisponível. Em uma conversa nova, a mesma pergunta recebeu a resposta correta. A reprodução pela rota `/chat` mostrou o mesmo efeito com “Qual a capital da França?”.

Causa provável: o atendimento guiado (`availability_conversation.availability_answer`) usa o contexto das últimas mensagens para decidir se a conversa trata de horários e só interrompe a coleta diante de termos informativos conhecidos (preço, endereço, pagamento etc.). Uma pergunta sem relação com a barbearia, mas com uma referência de data como “amanhã”, é tratada como continuação do agendamento. Nenhuma reserva é criada e as vagas exibidas são reais, mas a resposta não atende à pergunta. Correção sugerida: só reaproveitar o contexto quando a mensagem atual trouxer algum elemento de agendamento (serviço, data, período, horário ou profissional) e nenhum sinal de assunto novo, com teste de regressão.

## Detalhes por cenário

### A05 – IA desativada (Reprovado)

**Resultado esperado:** Consultas locais respondem; pergunta dependente da IA informa indisponibilidade sem resposta fabricada e registra unavailable.

**Passos e respostas observadas**

- Cliente: “Quais formas de pagamento vocês aceitam?” → Assistente: “Formas de pagamento cadastradas pela equipe: Dinheiro, Pix e cartão.”
- Cliente: “Quanto custa o corte?” → Assistente: “Serviços disponíveis Corte de Cabelo · R$ 50,00 · 40 min Cabelo + Barba · R$ 80,00 · 70 min Cabelo + Barba + Sobrancelha · R$ 100,00 · 90 min Pintar o Cabelo · R$ 200,00 · 120 min”
- Cliente: “Tem horário para corte dia 24/11?” → Assistente: “Encontrei estas opções disponíveis para Corte de Cabelo (R$ 50,00, 40 min). 24/11 · 08:00 · Alecio 24/11 · 08:30 · Alecio 24/11 · 09:00 · Alecio Escolha uma opção para revisar e confirmar a reserva.”
- Cliente: “Qual a previsão do tempo para amanhã?” → Assistente: “Encontrei estas opções disponíveis para Corte de Cabelo (R$ 50,00, 40 min). 19/11 · 08:00 · Alecio 19/11 · 08:30 · Alecio 19/11 · 09:00 · Alecio Escolha uma opção para revisar e confirmar a reserva.”
- Cliente: “Qual a previsão do tempo para amanhã?” → Assistente: “O atendimento com IA está indisponível. Posso consultar serviços, preços e vagas. Use Agendar para reservar ou peça atendimento da equipe.”

**Verificações**

- ✅ API sem chave da OpenAI (gpt_configured = false)
- ✅ Formas de pagamento vêm do cadastro (dinheiro, Pix e cartão)
- ✅ Preço do corte (R$ 50,00) vem do catálogo
- ✅ Consulta de vagas responde com opções reais sem IA
- ❌ Na mesma conversa, logo após a consulta de vagas, pergunta fora das respostas locais informa que a IA está indisponível
- ❌ Telemetria registra um evento unavailable para essa pergunta
- ✅ Em conversa nova, a pergunta fora do escopo informa que a IA está indisponível, sem resposta fabricada
- ✅ Telemetria registra o evento unavailable da conversa nova
- ✅ Nenhuma reserva criada

**Evidências:** [A05_contexto_anterior](evidencias/A05_contexto_anterior.png) · [A05_ia_desativada](evidencias/A05_ia_desativada.png)

### A06 – Restrições de horário sem IA (Aprovado)

**Resultado esperado:** Cada expressão segue a semântica definida, considerando duração, expediente e bloqueios; nenhuma vaga inventada.

**Passos e respostas observadas**

- “Quero corte dia 24/11 depois das 15h” → 15:30–16:10 Alecio; 16:00–16:40 Alecio; 16:30–17:10 Alecio
- “Quero corte dia 24/11 a partir das 15h” → 15:00–15:40 Alecio; 15:30–16:10 Alecio; 16:00–16:40 Alecio
- “Quero corte dia 24/11 antes das 15h” → 08:00–08:40 Alecio; 08:30–09:10 Alecio; 09:00–09:40 Alecio
- “Quero corte dia 24/11 entre 10h e 12h” → 10:00–10:40 Alecio; 10:30–11:10 Alecio; 11:00–11:40 Alecio
- “Quero corte dia 24/11 às 15h” → 15:00–15:40 Alecio
- “Quero pintar o cabelo dia 24/11 antes das 15h” → 08:00–10:00 Alecio; 08:30–10:30 Alecio; 09:00–11:00 Alecio
- “Quero corte no último horário de sábado” → 21/11 19:00 Rafael; 21/11 18:30 Rafael; 21/11 18:00 Rafael
- Maior início real no sábado, segundo /availability: 19:00
- Bloqueio das 18h às 20h de sábado para todos os barbeiros (3 bloqueios criados pela API da equipe)
- Repetição com o bloqueio → 17:00–17:40 Rafael; 16:30–17:10 Rafael; 16:00–16:40 Rafael

**Verificações**

- ✅ “depois das 15h”: opções reais na data pedida e dentro da restrição
- ✅ “a partir das 15h”: opções reais na data pedida e dentro da restrição
- ✅ “antes das 15h”: opções reais na data pedida e dentro da restrição
- ✅ “entre 10h e 12h”: opções reais na data pedida e dentro da restrição
- ✅ “às 15h”: opções reais na data pedida e dentro da restrição
- ✅ Pintura (120 min) antes das 15h só oferece horários que terminam até 12h, sem atravessar o almoço
- ✅ Último horário de sábado: opções do sábado, começando pelo maior início real e em ordem decrescente
- ✅ Com o bloqueio, o último horário termina até 18h
- ✅ Nenhuma reserva criada pelas consultas

### A07 – Mudança de contexto temporal (Aprovado)

**Resultado esperado:** A preferência mais recente substitui a anterior; trocar só o profissional mantém a restrição; horários inválidos pedem esclarecimento.

**Passos e respostas observadas**

- “Quero corte dia 24/11 pela manhã” → “depois das 15h” → 15:30 Alecio; 16:00 Alecio; 16:30 Alecio
- “Quero corte dia 24/11 depois das 15h” → “pela manhã” → 08:00 Alecio; 08:30 Alecio; 09:00 Alecio
- “Quero corte dia 24/11 às 15h” → “à tarde” → 13:30 Alecio; 14:00 Alecio; 14:30 Alecio
- “Quero corte dia 24/11 às 15h” → “com o Diego” → 15:00 Diego
- “Quero corte dia 24/11 às 25h” → Não consegui interpretar esse horário com segurança. Informe, por exemplo, às 15h, depois das 15h, a partir da
- “Quero corte dia 24/11 entre 16h e 10h” → Não consegui interpretar esse horário com segurança. Informe, por exemplo, às 15h, depois das 15h, a partir da
- “Quero corte dia 24/11 por volta das 15h” → Não consegui interpretar esse horário com segurança. Informe, por exemplo, às 15h, depois das 15h, a partir da

**Verificações**

- ✅ “pela manhã” seguido de “depois das 15h”: vale a restrição mais recente
- ✅ Ordem inversa: vale o período da manhã
- ✅ “às 15h” trocado por “à tarde”: opções da tarde, não apenas 15h
- ✅ Trocar só o profissional mantém “às 15h”
- ✅ “às 25h”: pede esclarecimento sem oferecer vagas
- ✅ “entre 16h e 10h”: pede esclarecimento sem oferecer vagas
- ✅ “por volta das 15h”: pede esclarecimento sem oferecer vagas
- ✅ Nenhuma reserva criada pelas consultas

### A02 – Sem vagas e alternativas (Aprovado)

**Resultado esperado:** Não promete nem grava horário ocupado; oferece outra data ou profissional existente; com a janela cheia, informa a indisponibilidade sem inventar vaga.

**Passos e respostas observadas**

- Cliente: “Quero corte com o Rafael dia 25/11 à tarde” → Assistente: “Não encontrei a combinação pedida. Estas são alternativas disponíveis para Corte de Cabelo (R$ 50,00, 40 min). 25/11 · 13:30 · Alecio 25/11 · 14:00 · Alecio 25/11 · 14:30 · Alecio Escolha uma opção para revisar e confirm”
- Alternativas exibidas: 25/11 · 13:30 · Alecio; 25/11 · 14:00 · Alecio; 25/11 · 14:30 · Alecio
- Cliente: “Quero corte amanhã” → Assistente: “Não encontrei vagas para esse horário e essas escolhas no período aberto. Prefere outro horário, período do dia ou profissional? A equipe também pode ajudar.”
- Aba Agendar: Não há horários para essa escolha no período exibido.

**Verificações**

- ✅ Dia 25/11 inteiro bloqueado para o Rafael pela API da equipe
- ✅ A disponibilidade do Rafael na data ficou vazia
- ✅ O assistente informa que a combinação pedida não tem vagas
- ✅ Cada alternativa existe de fato na disponibilidade (outro profissional ou outro dia)
- ✅ Consultar alternativas não cria reserva
- ✅ Janela de 19/11 a 25/11 bloqueada para os 3 barbeiros
- ✅ Com a janela cheia, informa a indisponibilidade sem inventar vaga
- ✅ A aba Agendar não exibe horários com a janela bloqueada
- ✅ Nenhuma reserva criada no cenário

**Evidências:** [A02_alternativas](evidencias/A02_alternativas.png) · [A02_janela_cheia](evidencias/A02_janela_cheia.png)

### A03 – Solicitação de retorno da equipe (Aprovado)

**Resultado esperado:** A frase só oferece ajuda; abrir ou abandonar o formulário não cria protocolo; campos vazios impedem o envio; só o envio válido cria o protocolo e registra escalated.

**Passos e respostas observadas**

- Cliente: “Quero falar com uma pessoa da equipe sobre uma reclamação” → Assistente: “Posso orientar você a falar com a equipe. Clique em Falar com a equipe e confirme o formulário para solicitar atendimento.”

**Verificações**

- ✅ O assistente orienta a usar Falar com a equipe
- ✅ A frase registra apenas a oferta (offered), sem chamado nem escalated
- ✅ Abrir e abandonar o formulário não cria chamado
- ✅ Envio sem contato é recusado com orientação
- ✅ Envio inválido não cria chamado
- ✅ A interface informa o retorno por telefone e que o assistente continua virtual
- ✅ O envio válido cria um chamado e um evento escalated
- ✅ O chamado fica aberto na fila da equipe, canal web

**Evidências:** [A03_retorno_equipe](evidencias/A03_retorno_equipe.png)

### A01 – Reserva confirmada (site) (Aprovado)

**Resultado esperado:** Antes da confirmação não existe reserva nova; depois existe exatamente uma, com preço e duração do resumo, e um evento booking_confirmed.

**Passos e respostas observadas**

- Agendar: serviço Corte de Cabelo, barbeiro Sem preferência, primeiro dia aberto (19/11)
- Horário 15:00 com Alecio
- Resumo: 3. Revise e confirme Corte de Cabelo com Alecio Quinta, 19/11/2026 às 15h00 · 40 min · R$ 50,00 Confirmar agendamento
- Parte do Telegram (/agendar): não executada nesta rodada, pois depende do token do bot

**Verificações**

- ✅ Antes de confirmar não existe reserva nova
- ✅ Depois da confirmação existe exatamente uma reserva nova e um evento booking_confirmed
- ✅ Reserva #1 gravada com Corte de Cabelo, 15:00, R$ 50,00 e 40 min, como no resumo
- ✅ A reserva ficou vinculada à sessão do site (booking_owners, canal web)
- ✅ Meus horários mostra a reserva na mesma sessão, sem pedir telefone ou código

**Evidências:** [A01_revisao](evidencias/A01_revisao.png) · [A01_comprovante](evidencias/A01_comprovante.png) · [A01_meus_horarios](evidencias/A01_meus_horarios.png)

### A08 – Sugestão clicável e confirmação (Aprovado)

**Resultado esperado:** O clique revalida catálogo e disponibilidade e preenche a revisão; nenhum clique cria reserva; só a confirmação grava.

**Passos e respostas observadas**

- Cliente: “Quero agendar corte dia 20/11 à tarde” → Assistente: “Encontrei estas opções disponíveis para Corte de Cabelo (R$ 50,00, 40 min). 20/11 · 13:30 · Alecio 20/11 · 14:00 · Alecio 20/11 · 14:30 · Alecio Escolha uma opção para revisar e confirmar a reserva.”
- Sugestões: 20/11 · 13:30 · Alecio; 20/11 · 14:00 · Alecio; 20/11 · 14:30 · Alecio
- Preço do corte restaurado para R$ 50,00
- Cliente: “Quero agendar corte dia 20/11 à tarde” → Assistente: “Encontrei estas opções disponíveis para Corte de Cabelo (R$ 50,00, 40 min). 20/11 · 13:30 · Diego 20/11 · 14:00 · Diego 20/11 · 14:30 · Diego Escolha uma opção para revisar e confirmar a reserva.”
- Nova consulta: 20/11 · 13:30 · Diego; 20/11 · 14:00 · Diego; 20/11 · 14:30 · Diego
- Reserva #3 criada: 20/11 13:30 com Diego, R$ 50

**Verificações**

- ✅ O chat apresenta sugestões clicáveis
- ✅ O clique abre Agendar com 13:30 e Alecio preenchidos para revisão
- ✅ O clique não cria reserva
- ✅ Outra sessão ocupa a vaga 14:00 com Alecio pela API
- ✅ A sugestão antiga é revalidada e o site avisa que a vaga não está mais disponível
- ✅ Clicar na sugestão antiga não cria reserva
- ✅ O dono altera o preço do corte para R$ 55,00
- ✅ Com o preço alterado, a sugestão antiga não abre a revisão e o site orienta nova consulta
- ✅ Clicar na sugestão com preço desatualizado não cria reserva
- ✅ A nova consulta não oferece mais a vaga ocupada (14:00 com Alecio)
- ✅ A sugestão atualizada abre a revisão de 13:30 com Diego
- ✅ Nenhum dos cliques criou reserva
- ✅ Somente a confirmação explícita cria a reserva (uma única)

**Evidências:** [A08_sugestao_preenchida](evidencias/A08_sugestao_preenchida.png) · [A08_vaga_ocupada](evidencias/A08_vaga_ocupada.png) · [A08_preco_alterado](evidencias/A08_preco_alterado.png) · [A08_confirmada](evidencias/A08_confirmada.png)

### A09 – Consulta e alteração de reservas por mensagem (Aprovado)

**Resultado esperado:** Só reservas da sessão; com mais de uma, pergunta qual; nada muda antes da confirmação final separada; “não” mantém a reserva; “sim” aplica a alteração.

**Passos e respostas observadas**

- Cliente: “meus agendamentos” → Assistente: “Suas reservas vinculadas a esta conversa: reserva #1: Corte de Cabelo com Alecio, 19/11/2026 das 15:00 às 15:40, R$ 50,00 reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00 Se quiser, peça par”
- Cliente: “quero remarcar minha reserva” → Assistente: “Qual reserva você quer remarcar? Escolha o número da lista ou escreva reserva #id: reserva #1: Corte de Cabelo com Alecio, 19/11/2026 das 15:00 às 15:40, R$ 50,00 reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:”
- Cliente: “reserva #1” → Assistente: “Para qual nova data você quer remarcar? Pode dizer amanhã ou informar dia/mês; se quiser, diga também o horário ou período do dia.”
- Cliente: “24/11 à tarde” → Assistente: “Encontrei estas opções para remarcar: 24/11/2026 às 13:30 com Alecio 24/11/2026 às 14:00 com Alecio 24/11/2026 às 14:30 com Alecio Escolha pelo número ou escreva o horário. Depois vou pedir sua confirmação final.”
- Cliente: “1” → Assistente: “Reserva atual: reserva #1: Corte de Cabelo com Alecio, 19/11/2026 das 15:00 às 15:40, R$ 50,00. Novo horário: 24/11/2026 das 13:30 às 14:10 com Alecio. O serviço e o valor permanecem Corte de Cabelo, R$ 50,00. Confirma a”
- Cliente: “sim” → Assistente: “Remarcação confirmada: reserva #1: Corte de Cabelo com Alecio, 24/11/2026 das 13:30 às 14:10, R$ 50,00.”
- Cliente: “quero cancelar minha reserva” → Assistente: “Qual reserva você quer cancelar? Escolha o número da lista ou escreva reserva #id: reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00 reserva #1: Corte de Cabelo com Alecio, 24/11/2026 das 13:”
- Cliente: “reserva #3” → Assistente: “Você quer cancelar a reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00. Confirma o cancelamento? Responda sim ou não.”
- Cliente: “não” → Assistente: “Tudo bem, encerrei este pedido. Sua reserva não foi alterada.”
- Cliente: “quero cancelar minha reserva” → Assistente: “Qual reserva você quer cancelar? Escolha o número da lista ou escreva reserva #id: reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00 reserva #1: Corte de Cabelo com Alecio, 24/11/2026 das 13:”
- Cliente: “reserva #3” → Assistente: “Você quer cancelar a reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00. Confirma o cancelamento? Responda sim ou não.”
- Cliente: “sim” → Assistente: “Cancelamento confirmado da reserva #3: Corte de Cabelo com Diego, 20/11/2026 das 13:30 às 14:10, R$ 50,00.”

**Verificações**

- ✅ A consulta lista as duas reservas desta sessão
- ✅ Com duas reservas, o assistente pergunta qual delas alterar
- ✅ O assistente oferece horários reais para a remarcação
- ✅ Depois da escolha, pede confirmação final separada
- ✅ Antes do “sim”, a reserva não muda
- ✅ Com o “sim”, a reserva #1 passa para 24/11 às 13:30
- ✅ O cancelamento apresenta a reserva e pede confirmação
- ✅ Com o “não”, a reserva continua confirmada
- ✅ Com o “sim”, a reserva #3 é cancelada
- ✅ Meus horários reflete as alterações feitas por mensagem
- ✅ Nenhuma reserva nova foi criada pelas alterações

**Evidências:** [A09_remarcada](evidencias/A09_remarcada.png) · [A09_cancelada](evidencias/A09_cancelada.png) · [A09_meus_horarios](evidencias/A09_meus_horarios.png)

### A10 – Isolamento entre sessões e recuperação sem o bot (Aprovado)

**Resultado esperado:** Outra sessão não vê nem altera as reservas; a recuperação informa que o Telegram não está configurado, sem simular envio.

**Passos e respostas observadas**

- Cliente: “quero cancelar minha reserva” → Assistente: “Posso localizar sua reserva sem precisar da equipe. Qual é o telefone completo com DDD usado no agendamento? Vou confirmar seu acesso pelo Telegram.”
- Cliente: “16900000101” → Assistente: “A verificação pelo Telegram ainda não está configurada nesta instalação.”

**Verificações**

- ✅ Uma sessão nova não vê as reservas de outra sessão
- ✅ Pedido de cancelamento em outra sessão inicia a verificação pelo telefone no Telegram
- ✅ Sem o bot configurado, o chat informa que a verificação não está disponível, sem simular envio
- ✅ A reserva da outra sessão continua confirmada
- ✅ Sem o token, o site informa que a verificação pelo Telegram não está configurada
- ✅ Nenhum desafio ou código de recuperação é criado sem o bot

**Evidências:** [A10_chat_outra_sessao](evidencias/A10_chat_outra_sessao.png) · [A10_recuperacao_sem_bot](evidencias/A10_recuperacao_sem_bot.png)

### A04 – Botões antigos do Telegram (Não executado)

**Resultado esperado:** O menu antigo não altera a escolha nem cria reserva; a confirmação repetida não duplica reserva ou evento.

Não executado: depende do token do bot e de um aplicativo Telegram (canal coberto apenas pelos testes automatizados com transporte simulado).

## Métricas operacionais do ensaio

Calculadas com `eval/export_logs.py` e `eval/metrics.py` sobre o banco de avaliação. O arquivo [`telemetria.csv`](telemetria.csv) tem as sessões pseudonimizadas e não contém mensagens nem contatos.

| Medida | Valor |
| --- | --- |
| Interações registradas | 44 em 21 sessões |
| Resultados das interações | answered: 36, unavailable: 1, offered: 1, escalated: 1, completed: 2, aborted: 1, verification_required: 2 |
| Encaminhamentos à equipe | 1 (1 sessão) |
| Reservas concluídas (eventos) | 3 |
| Latência por resposta (n = 43) | média 13.3 ms; mediana 12 ms; p95 29 ms |

A latência mede só o processamento no servidor, com respostas determinísticas e sem IA. Ela não representa o tempo com o modelo de linguagem nem o tempo percebido no navegador. As sessões são do ensaio automatizado e não correspondem a clientes.

## Limitações

- O canal Telegram (A04 e a parte de A01 pelo `/agendar`) não foi executado.
- A IA estava desativada; o comportamento com o modelo de linguagem não foi avaliado.
- O ensaio foi automatizado pelos próprios desenvolvedores, com dados sintéticos, e não mede compreensão ou satisfação de usuários.
