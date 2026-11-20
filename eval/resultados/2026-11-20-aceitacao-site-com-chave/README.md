# Ensaio de aceitação no site com a chave da OpenAI – 20/11/2026

> Ensaio executado pelos desenvolvedores com automação do navegador e dados fictícios. **Não** é avaliação com participantes e não substitui a avaliação humana de resolução descrita em [`eval/roteiro_automacao.md`](../../roteiro_automacao.md).

## Configuração

- **Código avaliado:** `main` em `0b3ad8d` (diagnóstico) e, nas demais execuções, o mesmo código com as correções desta branch (`avaliacao-com-credenciais`).
- **Data:** 20/11/2026, entre 12:01 e 12:57 (America/Sao_Paulo). Janela de reservas de 21/11 a 27/11/2026.
- **IA:** `gpt-5.4-mini` com a chave real da OpenAI e índice RAG gerado (15 trechos, `text-embedding-3-small`).
- **Telegram:** sem `TELEGRAM_BOT_TOKEN` nesta instância, porque o A10 verifica a recuperação sem o bot. O A04 continua pendente do aplicativo Telegram.
- **A05:** executado em instância separada, sem a chave (`AVAL_CENARIOS=A01,A05`).
- **Banco:** SQLite de avaliação recriado a cada execução, só com a conta de dono; clientes e telefones fictícios.

## Resultado por execução

Verificações aprovadas por cenário (– = não executado nesta instância).

| Execução | A01 | A02 | A03 | A05 | A06 | A07 | A08 | A09 | A10 | A04 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Diagnóstico, antes das correções (12:01) | 5/5 | 6/9 | 7/8 | – | 3/9 | 2/8 | 13/13 | 11/11 | 6/6 | – |
| Após a correção 1, A (12:18) | 5/5 | 9/9 | 8/8 | – | 8/9 | 4/8 | 13/13 | 11/11 | 6/6 | – |
| Após a correção 1, B (12:23) | 5/5 | 9/9 | 8/8 | – | 7/9 | 7/8 | 13/13 | **8/11** | 6/6 | – |
| Correções 1 e 2, rodada 1 (12:40) | 5/5 | 8/9 | 8/8 | – | 9/9 | 5/8 | 13/13 | 11/11 | 6/6 | – |
| Correções 1 e 2, rodada 2 (12:43) | 5/5 | 9/9 | 8/8 | – | 9/9 | 5/8 | 13/13 | 11/11 | 6/6 | – |
| Correções 1 a 3, rodada 3 (12:48) | 5/5 | 9/9 | 8/8 | – | 9/9 | 4/8 | 13/13 | 11/11 | 6/6 | – |
| Correções 1 a 3, rodada 4 (12:52) | 5/5 | 9/9 | 8/8 | – | 9/9 | 6/8 | 13/13 | 11/11 | 6/6 | – |
| Sem a chave, correções 1 a 3 (12:56) | 5/5 | – | – | 3/3 | – | – | – | – | – | – |

Os `resultados.json` de cada execução estão em [`execucoes/`](execucoes/). As evidências e a telemetria são da rodada 4.

## Defeitos encontrados e correções

1. **Sugestões clicáveis ignoravam o horário pedido.** A ferramenta `buscar_disponibilidade` guardava as três primeiras vagas do dia, sem aplicar a preferência. Para “depois das 15h”, “às 15h” ou “último horário de sábado”, os botões mostravam 08:00, 08:30 e 09:00, enquanto o texto do agente citava outros horários. Quando não havia vagas, o site exibia “Escolha uma opção…” sem opções, e o agente não oferecia alternativas (A02). **Correção:** a ferramenta recebe a preferência do cliente (`preferencia_horario`) e aplica o mesmo interpretador determinístico das remarcações; sem vaga, procura outro profissional na mesma data e, depois, as datas seguintes; os botões só aparecem quando há sugestões.
2. **Confirmação dependia de o modelo chamar a ferramenta.** Na execução “após a correção 1, B”, o agente não chamou `verificar_fluxo_de_reserva` nas respostas “1” e “sim” da remarcação e improvisou “confirme em Agendar”: a reserva não mudou (nenhuma alteração indevida), mas o pedido ficou travado. **Correção:** com uma remarcação ou um cancelamento pendente na sessão, a resposta do cliente vai direto ao fluxo determinístico, como já acontecia no Telegram.
3. **Instruções do agente.** Acrescentado que “corte” sem complemento é o serviço Corte de Cabelo e que, quando o cliente muda só o horário ou o profissional, o serviço e a data do histórico são mantidos.

Cada correção tem testes automatizados (`tests/test_conversation.py` e `tests/test_api.py`). O teste da correção 2 reproduz um agente que “esquece” a ferramenta e falha sem a correção.

## Adaptação do script à versão com IA

O script foi escrito para o chat guiado por regras. Para a versão com o agente, as verificações que dependiam de frases exatas passaram a verificar o comportamento:

- A02: indisponibilidade reconhecida por negação de vagas e ausência de sugestões, não pela frase “Não encontrei…”;
- A03: o evento `offered` não existe mais; verifica-se que a frase não cria chamado nem `escalated`;
- A07: barbeiro “Diego” trocado por “Caetano” (renomeado no código); pedido de esclarecimento reconhecido por pergunta ou pedido de novo horário (a partir da rodada 3, o critério passou a aceitar também “me diga”, “informe” e “prefere”);
- A09: opções e confirmação reconhecidas pelo formato de data e hora e pelas palavras “confirm” e “sim”, além das conferências no banco.

## Revisão manual do A07 nas rodadas 3 e 4

O A07 verifica a conversa em vários turnos e é o cenário que mais depende do modelo. Revisando as respostas:

| Verificação | Rodada 3 | Rodada 4 |
| --- | --- | --- |
| “pela manhã” → “depois das 15h” | aprovada | aprovada |
| “depois das 15h” → “pela manhã” | aprovada | aprovada |
| “às 15h” → “à tarde” | **falha real**: perguntou de novo o serviço | aprovada |
| “às 15h” → “com o Caetano” | **falha real**: consultou sem o barbeiro e ofereceu 15:00 com Alecio | aprovada |
| “às 25h” | aprovada | aprovada |
| “entre 16h e 10h” | falso negativo do critério: pediu esclarecimento (“Você pode me dizer…”) sem oferecer vagas | **falha real**: reinterpretou como 10h–16h e ofereceu 10:00, 10:30 e 11:00 |
| “por volta das 15h” | falso negativo do critério: pediu esclarecimento sem oferecer vagas | falso negativo do critério: pediu esclarecimento sem oferecer vagas |

Em nenhuma execução o agente criou, alterou ou cancelou reservas sem a confirmação do cliente, e todas as vagas oferecidas existiam na agenda.

## Telemetria da rodada 4

Exportada com `eval/export_logs.py` em [`telemetria.csv`](telemetria.csv) (sessões pseudonimizadas, sem o texto das mensagens):

| Medida | Valor |
| --- | --- |
| Interações registradas | 39, em 19 sessões |
| Respondidas | 33 |
| Alterações concluídas e desistências por mensagem | 2 e 1 |
| Pedidos de verificação de acesso | 2 |
| Encaminhamentos à equipe | 1 |
| Reservas concluídas | 3 (uma criada pela API para ocupar uma vaga no A08) |
| Latência média, mediana e p95 no servidor (n = 38) | 2,47 s; 2,95 s; 4,17 s |

A latência mede o processamento no servidor, incluindo as chamadas à OpenAI; não inclui a renderização no navegador.

## Pendências

- A04 e a validação real do Telegram (`docs/validacao-telegram-real.md`), que dependem do aplicativo no celular.
- Avaliação com participantes, cenários de FCR e comparação com os *baselines*.
