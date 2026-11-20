# Classificação de intenções – 20/11/2026

- **Classificador:** `barber_bot.intent.classify_intent`, com `gpt-5.4-mini` e saída estruturada em dez intenções.
- **Conjunto:** as 150 frases sintéticas de [`eval/sentencas_intencao.csv`](../../sentencas_intencao.csv), quinze por intenção.
- **Execução:** `uv run python eval/run_intent_eval.py`, em 20/11/2026, das 11:51 às 11:53 (America/Sao_Paulo), sobre a `main` em `0b3ad8d`. Previsões em [`resultados_intencao.csv`](resultados_intencao.csv).
- **Métricas:** `uv run python eval/metrics.py --intents eval/resultados/2026-11-20-intencoes/resultados_intencao.csv`.

## Resultado

| Medida | Valor |
| --- | --- |
| Frases | 150 |
| Acertos | 139 |
| Acurácia | 92,7% |
| F1 macro | 0,924 |

## Matriz de confusão (linhas: intenção verdadeira; colunas: prevista)

| Verdadeira \ Prevista | agendar | atend. humano | cancelar | cons. agend. | cons. disp. | fora escopo | políticas | remarcar | saudação | serv. preços |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agendar | **13** | | | 1 | | 1 | | | | |
| atendimento_humano | | **15** | | | | | | | | |
| cancelar | | | **15** | | | | | | | |
| consultar_agendamento | | | | **15** | | | | | | |
| consultar_disponibilidade | | | | | **15** | | | | | |
| fora_escopo | | | | | | **15** | | | | |
| politicas | | 2 | | | 2 | | **9** | | | 2 |
| remarcar | | | | | | | | **15** | | |
| saudacao | | 2 | | | | | | | **13** | |
| servicos_precos | | | | | | 1 | | | | **14** |

## Erros

| Frase | Verdadeira | Prevista |
| --- | --- | --- |
| Olá, tem alguém aí? | saudacao | atendimento_humano |
| Saudações, gostaria de falar com a barbearia. | saudacao | atendimento_humano |
| O serviço de pintura custa quanto? | servicos_precos | fora_escopo |
| Gostaria de agendar uma pintura. | agendar | fora_escopo |
| Pode confirmar uma reserva de cabelo e barba? | agendar | consultar_agendamento |
| Qual é o endereço da barbearia? | politicas | atendimento_humano |
| Qual o telefone para contato? | politicas | atendimento_humano |
| Quais são os dias de funcionamento? | politicas | servicos_precos |
| A barbearia fecha no horário do almoço? | politicas | consultar_disponibilidade |
| Como funciona o pacote mensal? | politicas | servicos_precos |
| Até que horas a barbearia atende? | politicas | consultar_disponibilidade |

Os erros se concentram em **políticas**, cujas frases sobre endereço, contato, funcionamento e pacotes ficam na fronteira com outras classes, e em pedidos de **pintura**, que o classificador tratou como fora do escopo por não saber, sem o cadastro, que a barbearia oferece esse serviço. Algumas confusões são defensáveis (“Olá, tem alguém aí?” também pode ser um pedido de atendimento), o que indica que parte dos erros vem da própria rotulação do conjunto.

O conjunto é sintético e equilibrado: o resultado compara classificadores em condições controladas, mas não representa a distribuição real das mensagens de clientes.
