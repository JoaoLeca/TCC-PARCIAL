# Roteiro de aceitação e avaliação da automação

Este roteiro descreve procedimentos a executar. Não contém resultados de voluntários nem comprovação de que os cenários manuais já foram executados. Testes automatizados usam dados sintéticos e não substituem a avaliação humana de resolução.

## Preparação reproduzível

1. Use uma instalação local de avaliação, com banco separado de clientes reais. Registre o commit, data e hora, fuso, canal, modelo, configuração da janela de agendamento e versão da base de conhecimento. Mantenha a mesma configuração nas comparações.
2. Configure `DATABASE_PATH` para o banco de avaliação em todos os processos e inicie API, interface e, se disponível, bot privado do Telegram conforme o README. Crie uma conta de equipe nesse banco para conferir reservas e protocolos. Use nomes e telefones fictícios controlados.
3. Para cenários com IA, configure a chave real apenas no `.env` local e prepare o índice RAG conforme o README. Não copie chaves, token do bot ou códigos privados para o relatório. Sem acesso à IA ou ao Telegram, marque os respectivos cenários como **não executados — pendentes de credenciais** e registre a limitação. A validação local com banco sintético e consultas determinísticas sem IA deve ser apresentada separadamente; não comprova integração com os serviços reais.
4. Escolha datas entre os limites configurados, respeitando dia de funcionamento, escala e duração do serviço. Registre as datas concretas e os horários usados. Para comparar execuções, restaure uma cópia do banco de avaliação com a mesma ocupação; preserve as evidências de cada execução antes de restaurar.
5. Execute `uv run pytest`. Guarde o resultado com a versão avaliada. Execute os cenários abaixo no site e, onde indicado, no Telegram. Confirme o efeito no painel da equipe; texto afirmativo do assistente não comprova gravação.

## Cenários de aceitação

| ID | Preparação e passos | Resultado a verificar | Evidência mínima |
| --- | --- | --- | --- |
| A01 — Reserva confirmada | No site, peça um corte, escolha profissional/data/vaga, informe dados fictícios e revise o resumo. Pare antes da confirmação e confira o painel. Confirme uma única vez. Repita pelo `/agendar` do Telegram em outra vaga. | Antes da confirmação não existe reserva nova. Depois existe exatamente uma reserva no horário e profissional escolhidos, com preço e duração do resumo. O comprovante permite consultar essa reserva. Há um evento `booking_confirmed` por reserva e sessão. | Quantidade de reservas antes/depois, ID interno da reserva no registro privado de teste, canal, horário e comprovante com telefone/código ocultados na versão compartilhada. |
| A02 — Sem vagas e alternativas | No banco de avaliação, ocupe ou bloqueie todas as vagas de um profissional em uma data válida; mantenha vaga em outra data/profissional. Solicite o período cheio pelo chat e pelo fluxo guiado. Escolha uma alternativa oferecida. Repita com toda a janela sem vagas. | Não promete nem grava o horário ocupado. Oferece mudança de data ou profissional quando possível; a alternativa realmente aparece na disponibilidade. Com a janela cheia, informa a indisponibilidade e o próximo caminho sem inventar vaga. Consultar alternativas não cria reserva. | Disponibilidade antes/depois, alternativas exibidas e contagem de reservas inalterada até confirmar. |
| A03 — Solicitação de retorno da equipe | No chat do site, envie “Quero falar com uma pessoa da equipe sobre uma reclamação”. Confira o painel; abra **Falar com equipe**, volte à conversa e confira novamente. Reabra, tente enviar sem contato e depois preencha nome e telefone fictícios válidos e pressione **Solicitar atendimento**. | A frase oferece ajuda (`offered`), sem criar protocolo nem `escalated`. Abrir ou abandonar o formulário mantém apenas um rascunho. Campos ausentes impedem o envio. Só o envio válido cria solicitação/protocolo verificável e registra `escalated`. A interface explica que a equipe retorna pelo telefone informado e que o assistente continua sendo virtual; não promete troca por uma pessoa no chat nem resolução da reclamação. | Contagem de solicitações e eventos antes/depois de cada etapa, protocolo após envio e texto de orientação, sem contatos no relatório. |
| A04 — Botões antigos do Telegram | Use `/agendar`, avance até visualizar vagas e guarde esse menu. Inicie outro `/agendar`; clique em uma vaga do menu antigo. Depois conclua uma reserva no fluxo atual e clique novamente no botão antigo de confirmação. | O menu antigo não altera a escolha atual nem cria reserva. A confirmação repetida não duplica reserva ou evento de conclusão. Recebe orientação para retomar o fluxo vigente ou consultar comprovante. | Capturas dos dois menus com dados privados ocultados e contagem de reservas/eventos antes/depois de cada clique. |
| A05 — IA desativada | Em uma instância separada, remova temporariamente a chave OpenAI local e reinicie os processos. No site, consulte serviços/preços e disponibilidade por texto; envie também pergunta que exija a IA e esteja fora das respostas locais. Use o formulário de agendamento. Repita no Telegram somente se houver credenciais reais para esse canal. | FAQ, catálogo e disponibilidade cobertos pelo modo determinístico continuam respondendo com os dados locais. Pergunta dependente da IA informa indisponibilidade e registra `unavailable`, sem resposta fabricada. Uma reserva guiada só gera evento de conclusão após confirmação e gravação. | Respostas locais e de indisponibilidade, telemetria e reserva no banco de avaliação. Restaure a configuração local depois. |
| A06 — Restrições de horário sem IA | Sem chave OpenAI, peça corte em uma data válida com “depois das 15h”, “a partir das 15h”, “antes das 15h”, “entre 10h e 12h” e “às 15h”. Consulte também “último horário de sábado”. Confira escala, bloqueios, duração e disponibilidade no banco. Repita com o maior horário bloqueado e com serviço de duração maior. | “Depois” exige início estritamente maior que 15h; “a partir” aceita início às 15h; “antes” exige término até 15h; “entre” exige início a partir de 10h e término até 12h. Horário exato não vira horário aproximado. “Último” ordena do início válido mais tardio para os anteriores no sábado permitido pela janela. Todo serviço cabe integralmente no expediente e fora dos bloqueios. Se não houver vaga compatível, não inventa uma. | Texto enviado, data concreta, início/fim das sugestões e comparação com disponibilidade/catalogação vigente. Contagem de reservas inalterada. |
| A07 — Mudança de contexto temporal | Peça corte em uma data válida “pela manhã”; depois diga “depois das 15h”. Faça nova conversa com a ordem inversa. Troque “às 15h” por “à tarde”; em outra conversa, mantenha “às 15h” e mude apenas o profissional. Envie também “às 25h”, “entre 16h e 10h” e “por volta das 15h”. | A preferência temporal mais recente substitui a anterior. Mudar apenas profissional preserva a restrição de horário. Alternativas de profissional/data também respeitam essa restrição. Horários inválidos ou não suportados pedem esclarecimento sem oferecer vagas que ignorem o pedido. | Histórico mínimo, restrição esperada e horários retornados; nenhuma reserva criada por consultas. |
| A08 — Sugestão clicável e confirmação | No chat do site, consulte disponibilidade, clique numa sugestão e confira o resumo/formulário preenchido. Pare antes de **Confirmar agendamento**. Em outra execução, guarde uma sugestão e ocupe sua vaga em outra sessão; repita alterando preço/duração do serviço ou tornando o serviço/profissional indisponível antes do clique. Reconsulte uma vaga válida e confirme uma única vez. | O clique verifica disponibilidade e catálogo atuais e preenche serviço, profissional e horário compatíveis para revisão. Sugestão antiga não valida vaga ocupada nem mantém preço/duração obsoletos; alteração incompatível orienta nova consulta/revisão. Nenhum clique em sugestão cria reserva. Somente **Confirmar agendamento**, com dados válidos e disponibilidade revalidada, cria a reserva. | Sugestão original, alteração controlada e resposta ao clique; resumo atualizado e contagem de reservas antes/depois do clique e da confirmação. |

Em cada execução, registre `run_id`, cenário, canal, data/hora, configuração, passos efetivamente seguidos, resultado esperado/observado, aprovado/reprovado/não executado e justificativa. Use um identificador local de participante em avaliações com voluntários. Evidências compartilhadas devem ocultar telefones, nomes de clientes, chat IDs e códigos de acesso.

## Exportação e métricas operacionais

Na raiz do projeto, após as execuções:

```bash
uv run python eval/export_logs.py --out eval/telemetria_avaliacao.csv
uv run python eval/metrics.py --latency eval/telemetria_avaliacao.csv --operations eval/telemetria_avaliacao.csv
```

Para analisar um banco de avaliação específico, use `--database caminho/do/banco.db` no exportador. O CSV preserva as colunas antigas `session_id`, `created_at`, `intent` e `response_ms`, acrescentando `channel`, `outcome` e `tools_called`. Não exporta mensagens, nomes, telefones nem códigos privados. O identificador de sessão é substituído por SHA-256 determinístico com prefixo `session_`; trata-se de pseudonimização, não anonimização irreversível. Horários e vinculação entre sessões também exigem cuidado no compartilhamento.

O canal vem da coluna `channel` quando disponível. Para bancos antigos, IDs com prefixo `telegram:` são classificados como Telegram; os demais são classificados como web. Dados legados sem distinção de canal não permitem reconstruir essa separação com certeza.

Os resultados têm os seguintes denominadores:

- `interactions`: registros de atendimento, excluindo eventos `booking_confirmed`. `outcomes` e `channels` distribuem essas interações; valor ausente fica `unknown`.
- `escalations` e `escalation_rate`: quantidade de interações `escalated` e divisão por todas as interações. `escalated_sessions` e `session_escalation_rate`: sessões distintas com escalonamento, divididas pelas sessões com identificador e pelo menos uma interação. Uma sessão com dois encaminhamentos conta duas interações e uma sessão escalada.
- `completed_bookings`: eventos de reservas efetivamente gravadas. O exportador elimina repetição do mesmo par sessão/reserva antes de contar. `booked_sessions` conta sessões distintas desses eventos. Não confundir reserva criada com comparecimento, pagamento ou reserva ainda ativa após eventual cancelamento.
- `errors` e `unavailable`: contagens de falha e indisponibilidade registradas pelo runtime. `answered` só informa que houve resposta; não comprova qualidade, resolução ou ausência de intervenção humana posterior.
- Latência: `n`, média, mediana e p95 em milissegundos, sem eventos de reserva. O p95 usa o posto `ceil(0,95 × n)` na lista ordenada. Valores vazios, inválidos, negativos ou não finitos não entram na amostra; informe `n` junto do resultado. Essa medida cobre o processamento instrumentado, sem garantir o tempo completo percebido no navegador ou na rede do Telegram.
- Duração de sessão: intervalo entre primeira e última interação registrada, em minutos, sem eventos de reserva. Uma sessão com uma interação tem duração zero. A identificação de sessão do site e a conversa persistente do Telegram podem ter ciclos diferentes; apresente os canais separadamente quando comparar duração ou taxas por sessão. Não interprete esses IDs como contagem de clientes ou contatos humanos.

Registros antigos sem outcomes continuam sendo aceitos, mas não sustentam inferências retroativas sobre escalonamentos ou reservas. Não concatene o mesmo export mais de uma vez: a deduplicação ocorre na origem/exportação, antes das métricas.

## Intenções e FCR com validação humana

O conjunto existente contém 150 frases em `eval/sentencas_intencao.csv`. Com IA configurada, execute:

```bash
uv run python eval/run_intent_eval.py
uv run python eval/metrics.py --intents eval/resultados_intencao.csv
```

Guarde o CSV de previsões, configuração do modelo e data. Relate acurácia, F1 macro, matriz de confusão e `n`; confira se as 150 frases têm previsão antes de comparar modelos. O cálculo existente usa linhas com rótulo verdadeiro e previsão preenchidos. Ausências devem ser descritas como falhas de execução, sem ocultar a redução da amostra. Separe conjunto de ajuste e conjunto de teste ao alterar classificador ou prompts.

Para FCR, copie os 30 cenários de `eval/cenarios_fcr.csv` por sistema/execução, preservando o original em branco. Dois avaliadores revisam cada conversa e seu efeito no sistema, preenchem as colunas individuais e reconciliam divergências nos campos de consenso:

| Campo de consenso | Critério |
| --- | --- |
| `expected_task_achieved` | `1` se o objetivo observável foi atingido; `0` caso contrário. Para agendar, conferir a reserva gravada. Para orientar sobre indisponibilidade, conferir regra e alternativa válidas. |
| `escalated` | `1` se precisou encaminhar para uma pessoa; `0` se não precisou. Encaminhar corretamente pode atingir a tarefa, mas continua sendo escalonamento. |
| `repeated_question` | `1` se houve repetição desnecessária de pergunta/dados para concluir o mesmo atendimento; `0` caso contrário. Não penalizar a confirmação explícita necessária à reserva. |

Somente cenários com os três campos de consenso preenchidos com `0` ou `1` entram no denominador. FCR = cenários com objetivo atingido, sem escalonamento e sem repetição / cenários completamente avaliados. Campos em branco não significam sucesso nem ausência de escalonamento. `answered` e `booking_confirmed` não preenchem esses rótulos automaticamente. Registre o número de cenários incompletos e apresente também taxa de tarefa atingida, pois encaminhamento pode ser o resultado correto para uma reclamação mesmo sem contar como FCR.

```bash
uv run python eval/metrics.py --fcr eval/fcr_execucao_preenchido.csv
```

Na avaliação de uso, obtenha consentimento e entregue aos participantes tarefas baseadas nesses 30 cenários, com os dados fictícios necessários, sem explicar quais botões usar ou como formular a resposta. Inclua consultar horário com limite preciso, escolher sugestão e revisar a reserva antes de confirmar, corrigir uma preferência e solicitar retorno humano. Distribua tarefas e ordem entre participantes e registre quais foram realmente aplicadas. As 150 frases são um conjunto de avaliação de intenções; não equivalem a 150 usuários nem substituem conversas observadas.

Observe sem orientar; se houver ajuda, registre o momento e o motivo. Para cada tarefa, anote conclusão independente/com ajuda/não concluída, erros observados, tempo do início à conclusão ou interrupção e dados/perguntas repetidos desnecessariamente. Confira o efeito no banco e peça ao participante que explique o próximo passo do retorno humano para verificar se compreendeu o contato por telefone. Registre falhas e comentários com identificador local, sem contatos pessoais. Apresente quantidade de participantes e tarefas, denominadores, distribuição dos tempos e resultados observados, sem criar metas percentuais ou preencher resultados ainda não coletados. Ensaios sintéticos do desenvolvedor continuam separados desta avaliação com pessoas.

Após as tarefas, use `eval/questionario_likert.md` com consentimento dos participantes, exportação sem nome/e-mail e coluna `rating` para o item analisado. Não apresente ausência de respostas como média zero.

## Comparação com os baselines existentes

Compare o sistema completo com `keyword_faq` e `llm_without_rag`, definidos em `eval/baselines.py`, usando as mesmas perguntas, ordem controlada e rubrica humana. O segundo faz chamadas pagas quando configurado e precisa da chave apenas no ambiente local. Ele responde sem ferramentas de agenda e sem recuperação RAG.

Para cada condição, registre entradas, respostas, tempo medido pelo mesmo relógio monotônico e configuração em arquivos privados separados. Um exemplo de chamada isolada, sem gravar credenciais:

```bash
uv run python -c "from eval.baselines import keyword_faq; print(keyword_faq('Quais formas de pagamento vocês aceitam?'))"
uv run python -c "from eval.baselines import llm_without_rag; print(llm_without_rag('Quais formas de pagamento vocês aceitam?'))"
```

Avalie precisão factual nas FAQs e FCR nas mesmas tarefas. Para tarefas transacionais, somente marque objetivo atingido quando houver efeito verificável no banco; uma resposta do baseline dizendo que agendou não vale como reserva. Declare a diferença de capacidade dos baselines e apresente separadamente resultados de FAQ e tarefas de agenda. Esses baselines não expõem classificador de intenção equivalente: não invente rótulos previstos para montar uma comparação de acurácia de intenções.

Apresente resultados por sistema e canal com tamanho da amostra, condições, dados faltantes e limitações. Identifique ensaios do desenvolvedor como testes de aceitação e ensaios com participantes como avaliação de uso. Preserve os dados brutos de forma privada e inclua no TCC apenas resultados efetivamente coletados e agregados.
